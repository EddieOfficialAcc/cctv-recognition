from contextlib import closing
import logging
from threading import Lock
from typing import Annotated, Literal

import pyodbc
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError

from app.auth import require_user
from app.database import DatabaseSettings, connect
from app.inspection import InvalidScreenshot, prepare_screenshot
from app.recorder import MAX_CHANNELS, Recorder, RecorderError, RecorderSettings

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
) -> CameraConfigurationPage:
    response.headers["Cache-Control"] = "no-store"
    brand_filter = " WHERE brand = ?" if brand is not None else ""
    brand_parameters = (brand,) if brand is not None else ()
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            total = cursor.execute(
                f"SELECT COUNT(*) FROM dbo.cctv_config{brand_filter}", *brand_parameters,
            ).fetchone()[0]
            rows = cursor.execute(
                "SELECT shop_code, shop_name, company, region, brand, internal_ip, port, cam_qty, "
                "CASE WHEN NULLIF(LTRIM(RTRIM(admin_name)), '') IS NOT NULL "
                "AND NULLIF(LTRIM(RTRIM(admin_password)), '') IS NOT NULL THEN 1 ELSE 0 END "
                f"FROM dbo.cctv_config{brand_filter} ORDER BY shop_code "
                "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
                *brand_parameters, (page - 1) * page_size, page_size,
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
            "WHERE brand = 'DAHAU' AND internal_ip IS NOT NULL "
            "AND NULLIF(LTRIM(RTRIM(shop_code)), '') IS NOT NULL "
            "AND NULLIF(LTRIM(RTRIM(region)), '') IS NOT NULL "
            "GROUP BY shop_code, region HAVING COUNT(*) = 1 AND MAX(cam_qty) >= 1 "
            "ORDER BY shop_code, region"
        ).fetchall()
    return [SnapshotRecorder(shop=row[0], region=row[1], name=row[2], channels=min(row[3], MAX_CHANNELS))
            for row in rows]


@router.post("/snapshot", response_class=Response)
def capture_snapshot(selection: SnapshotRequest) -> Response:
    password = RecorderSettings().dahau_password.get_secret_value()
    if not password or not password.strip():
        raise HTTPException(503, "Manual snapshot capture is not configured.")
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        rows = cursor.execute(
            "SELECT TOP (2) internal_ip, cam_qty FROM dbo.cctv_config "
            "WHERE brand = 'DAHAU' AND shop_code = ? AND region = ? AND internal_ip IS NOT NULL",
            selection.shop_code, selection.region,
        ).fetchall()
    if not rows:
        raise HTTPException(404, "DAHAU recorder not found.")
    if len(rows) != 1:
        raise HTTPException(409, "Multiple DAHAU recorders match this shop and region.")
    address, quantity = rows[0]
    if not isinstance(quantity, int) or selection.channel > quantity:
        raise HTTPException(422, "Channel is outside the configured camera count.")
    if not manual_capture_lock.acquire(blocking=False):
        raise HTTPException(429, "Another manual capture is in progress. Try again shortly.")
    try:
        image = prepare_screenshot(Recorder(address, "admin", password).snapshot(selection.channel))
    except (RecorderError, InvalidScreenshot) as error:
        logger.warning("Manual snapshot capture failed (%s).", type(error).__name__)
        raise HTTPException(502, "The camera did not return a valid snapshot.") from None
    finally:
        manual_capture_lock.release()
    return Response(content=image, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
