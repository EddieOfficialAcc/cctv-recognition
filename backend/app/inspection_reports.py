from contextlib import closing
from datetime import date, datetime, timezone
import logging
from typing import Annotated
from uuid import UUID

import pyodbc
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from app.auth import require_user
from app.database import DatabaseSettings, connect

logger = logging.getLogger(__name__)


def database_errors():
    try:
        yield
    except (pyodbc.Error, ValidationError) as error:
        logger.error("Inspection report database operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "Inspection report storage unavailable.") from None


class Run(BaseModel):
    run_date: date
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    total: int
    completed: int
    failed: int
    skipped: int
    interrupted: int
    saved: int


class Item(BaseModel):
    id: UUID
    recorder_index: int
    shop_code: str
    channel: int
    camera_name: str
    region: str
    status: str
    coverage_method: str
    message: str
    captured_at: datetime | None
    has_evidence: bool
    has_record: bool


class ItemPage(BaseModel):
    items: list[Item]
    total: int
    page: int
    page_size: int


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None else None


router = APIRouter(prefix="/api/inspection-runs", tags=["inspection runs"],
                   dependencies=[Depends(require_user), Depends(database_errors)])


@router.get("", response_model=list[Run])
def list_runs() -> list[Run]:
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        rows = cursor.execute(
            "SELECT TOP 100 r.run_date, r.status, r.started_at, r.finished_at, COUNT(i.id), "
            "SUM(CASE WHEN i.status='completed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN i.status='failed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN i.status='skipped' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN i.status='interrupted' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN i.evidence IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM dbo.inspection_runs r LEFT JOIN dbo.inspection_run_items i ON i.run_date=r.run_date "
            "GROUP BY r.run_date, r.status, r.started_at, r.finished_at ORDER BY r.run_date DESC"
        ).fetchall()
    return [Run(run_date=r[0], status=r[1], started_at=_utc(r[2]), finished_at=_utc(r[3]),
                total=r[4], completed=r[5], failed=r[6], skipped=r[7], interrupted=r[8], saved=r[9]) for r in rows]


@router.get("/{run_date}/items", response_model=ItemPage)
def list_items(
    run_date: date, page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ItemPage:
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        if cursor.execute("SELECT 1 FROM dbo.inspection_runs WHERE run_date=?", run_date).fetchone() is None:
            raise HTTPException(404, "Inspection run not found.")
        total = cursor.execute(
            "SELECT COUNT(*) FROM dbo.inspection_run_items WHERE run_date=?", run_date,
        ).fetchone()[0]
        rows = cursor.execute(
            "SELECT i.id, i.recorder_index, i.shop_code, i.channel, i.camera_name, i.region, i.status, "
            "i.coverage_method, i.message, i.captured_at, "
            "CASE WHEN i.evidence IS NULL THEN 0 ELSE 1 END, "
            "CASE WHEN r.id IS NULL THEN 0 ELSE 1 END "
            "FROM dbo.inspection_run_items i LEFT JOIN dbo.recognition_records r ON i.id=r.id "
            "WHERE i.run_date=? ORDER BY i.recorder_index, i.channel OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
            run_date, (page - 1) * page_size, page_size,
        ).fetchall()
    return ItemPage(items=[
        Item(id=r[0], recorder_index=r[1], shop_code=r[2], channel=r[3], camera_name=r[4],
             region=r[5], status=r[6], coverage_method=r[7], message=r[8], captured_at=_utc(r[9]),
             has_evidence=bool(r[10]), has_record=bool(r[11])) for r in rows
    ], total=total, page=page, page_size=page_size)


@router.get("/items/{item_id}/evidence")
def item_evidence(item_id: UUID) -> Response:
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        row = cursor.execute("SELECT evidence FROM dbo.inspection_run_items WHERE id=?", str(item_id)).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(404, "Inspection evidence not found.")
    return Response(content=bytes(row[0]), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
