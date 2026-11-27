from contextlib import closing
from datetime import datetime, timedelta, timezone
import logging
from threading import Lock
from typing import Annotated, Literal
from uuid import uuid4

import pyodbc
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError

from app.auth import require_user
from app.database import DatabaseSettings, connect
from app.inspection import InvalidScreenshot, prepare_screenshot
from app.recorder import MAX_CHANNELS, Recorder, RecorderError, RecorderSettings
from app.snapshot_storage import SnapshotStorageError, save_snapshot

logger = logging.getLogger(__name__)
manual_capture_lock = Lock()


def database_errors():
    try:
        yield
    except (pyodbc.Error, ValidationError) as error:
        logger.error("Camera configuration database operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "Camera configuration storage unavailable.") from None


class CameraConfiguration(BaseModel):
    shop_code: str | None
    shop_name: str | None
    company: str | None
    region: str | None
    brand: str | None
    internal_ip: str | None
    port: int | None
    cam_qty: int | None
    credentials_complete: bool


class CameraConfigurationPage(BaseModel):
    rows: list[CameraConfiguration]
    total: int
    page: int
    page_size: int


class SnapshotRequest(BaseModel):
    shop_code: str = Field(min_length=1, max_length=50)
    region: str = Field(min_length=1, max_length=20)
    channel: int = Field(strict=True, ge=1, le=MAX_CHANNELS)


class SnapshotRecorder(BaseModel):
    shop: str
    region: str
    name: str | None
    channels: int


router = APIRouter(
    prefix="/api/camera-configuration",
    tags=["camera configuration"],
    dependencies=[Depends(require_user), Depends(database_errors)],
)


@router.get("", response_model=CameraConfigurationPage)
def list_camera_configuration(
    response: Response,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    brand: Literal["DAHAU", "HIKVISION"] | None = None,
    region: Literal["Hong Kong", "Macau"] | None = None,
    company: Literal["BestMart360", "FoodVilla"] | None = None,
) -> CameraConfigurationPage:
    response.headers["Cache-Control"] = "no-store"
    filters = [(column, value) for column, value in (
        ("brand", brand), ("region", region), ("company", company),
    ) if value is not None]
    where = " WHERE " + " AND ".join(f"{column} = ?" for column, _ in filters) if filters else ""
    parameters = tuple(value for _, value in filters)
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            total = cursor.execute(
                f"SELECT COUNT(*) FROM dbo.cctv_config{where}", *parameters,
            ).fetchone()[0]
            rows = cursor.execute(
                "SELECT shop_code, shop_name, company, region, brand, internal_ip, port, cam_qty, "
                "CASE WHEN NULLIF(LTRIM(RTRIM(admin_name)), '') IS NOT NULL "
                "AND NULLIF(LTRIM(RTRIM(admin_password)), '') IS NOT NULL THEN 1 ELSE 0 END "
                f"FROM dbo.cctv_config{where} ORDER BY shop_code "
                "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
                *parameters, (page - 1) * page_size, page_size,
            ).fetchall()
    return CameraConfigurationPage(
        rows=[
            CameraConfiguration(
                shop_code=row[0], shop_name=row[1], company=row[2], region=row[3],
                brand=row[4], internal_ip=row[5], port=row[6], cam_qty=row[7],
                credentials_complete=bool(row[8]),
            )
            for row in rows
        ],
        total=total, page=page, page_size=page_size,
    )


@router.get("/snapshot-recorders", response_model=list[SnapshotRecorder])
def list_snapshot_recorders(response: Response) -> list[SnapshotRecorder]:
    response.headers["Cache-Control"] = "no-store"
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        rows = cursor.execute(
            "SELECT shop_code, region, MAX(shop_name), MAX(cam_qty) FROM dbo.cctv_config "
            "WHERE brand IN ('DAHAU', 'HIKVISION') AND internal_ip IS NOT NULL "
            "AND NULLIF(LTRIM(RTRIM(shop_code)), '') IS NOT NULL "
            "AND NULLIF(LTRIM(RTRIM(region)), '') IS NOT NULL "
            "GROUP BY shop_code, region HAVING COUNT(*) = 1 AND MAX(cam_qty) >= 1 "
            "ORDER BY shop_code, region"
        ).fetchall()
    return [SnapshotRecorder(shop=row[0], region=row[1], name=row[2], channels=min(row[3], MAX_CHANNELS))
            for row in rows]


@router.post("/snapshot", response_class=Response)
def capture_snapshot(selection: SnapshotRequest) -> Response:
    settings = RecorderSettings()
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        rows = cursor.execute(
            "SELECT TOP (2) brand, internal_ip, cam_qty, admin_name, admin_password FROM dbo.cctv_config "
            "WHERE brand IN ('DAHAU', 'HIKVISION') AND shop_code = ? AND region = ? AND internal_ip IS NOT NULL",
            selection.shop_code, selection.region,
        ).fetchall()
    if not rows:
        raise HTTPException(404, "Recorder not found.")
    if len(rows) != 1:
        raise HTTPException(409, "Multiple recorders match this shop and region.")
    row = rows[0]
    if len(row) >= 5:
        brand, address, quantity, username, db_password = row[:5]
    else:
        address, quantity = row[:2]
        brand, username, db_password = "DAHAU", "admin", ""
    if not isinstance(quantity, int) or selection.channel > quantity:
        raise HTTPException(422, "Channel is outside the configured camera count.")
    brand = (brand or "DAHAU").strip().upper()
    username = (username or "admin").strip()
    password = (db_password or "").strip()
    if not password:
        password = (settings.hikvision_password if brand == "HIKVISION" else settings.dahau_password).get_secret_value()
    if not password or not password.strip():
        raise HTTPException(503, "Manual snapshot capture is not configured.")
    if not manual_capture_lock.acquire(blocking=False):
        raise HTTPException(429, "Another manual capture is in progress. Try again shortly.")
    try:
        recorder = (Recorder(address, username, password, brand)
                    if brand != "DAHAU" else Recorder(address, username, password))
        image = prepare_screenshot(recorder.snapshot(selection.channel))
        capture_id = str(uuid4())
        day = datetime.now(timezone(timedelta(hours=8))).date()
        try:
            save_snapshot(settings.snapshot_dir / "manual", day, capture_id, selection.channel, image)
        except SnapshotStorageError:
            logger.error("Manual snapshot archive write failed.")
            raise HTTPException(503, "Snapshot captured but could not be saved to the local archive.") from None
    except (RecorderError, InvalidScreenshot) as error:
        logger.warning("Manual snapshot capture failed (%s).", type(error).__name__)
        raise HTTPException(502, "The camera did not return a valid snapshot.") from None
    finally:
        manual_capture_lock.release()
    return Response(content=image, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f'inline; filename="snapshot-{capture_id}.jpeg"'})
