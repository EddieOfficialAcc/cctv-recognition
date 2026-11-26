from contextlib import closing
import logging
from typing import Annotated

import pyodbc
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ValidationError

from app.auth import require_user
from app.database import DatabaseSettings, connect

logger = logging.getLogger(__name__)


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
) -> CameraConfigurationPage:
    response.headers["Cache-Control"] = "no-store"
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            total = cursor.execute("SELECT COUNT(*) FROM dbo.cctv_config").fetchone()[0]
            rows = cursor.execute(
                "SELECT shop_code, shop_name, company, region, brand, internal_ip, port, cam_qty, "
                "CASE WHEN NULLIF(LTRIM(RTRIM(admin_name)), '') IS NOT NULL "
                "AND NULLIF(LTRIM(RTRIM(admin_password)), '') IS NOT NULL THEN 1 ELSE 0 END "
                "FROM dbo.cctv_config ORDER BY shop_code "
                "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
                (page - 1) * page_size, page_size,
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
