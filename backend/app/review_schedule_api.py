from datetime import datetime, timezone
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_user
from app.recorder import RecorderSettings
from app.review_schedules import ReviewSchedule, ScheduleConflict, ScheduleInput, SchedulePage, ScheduleStore, ScheduleUpdate, next_run
from app.snapshot_analysis_api import queue_errors, require_vision

logger = logging.getLogger(__name__)


def schedule_errors():
    try:
        yield
    except ScheduleConflict as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        logger.info("Review schedule was rejected (%s).", type(error).__name__)
        raise HTTPException(422, "Schedule has no permitted execution within eight years. Check dates and holiday settings.") from None


router = APIRouter(prefix="/api/review-schedules", tags=["review schedules"],
                   dependencies=[Depends(require_user), Depends(schedule_errors), Depends(queue_errors)])


def _store() -> ScheduleStore:
    return ScheduleStore(RecorderSettings().snapshot_dir)


def _check_enabled(selection: ScheduleInput) -> None:
    if selection.enabled:
        require_vision()
        settings = RecorderSettings()
        if (not settings.dahau_password.get_secret_value().strip()
                and not settings.hikvision_password.get_secret_value().strip()):
            raise HTTPException(503, "Manual snapshot capture is not configured.")


@router.get("", response_model=SchedulePage)
def list_schedules() -> SchedulePage:
    return _store().page()


class Preview(BaseModel):
    next_runs: list[datetime]


@router.post("/preview", response_model=Preview)
def preview_schedule(selection: ScheduleInput) -> Preview:
    cursor = datetime.now(timezone.utc)
    dates = []
    for _ in range(3):
        cursor = next_run(selection.cron, cursor, selection.skip_holidays)
        dates.append(cursor)
    return Preview(next_runs=dates)


@router.post("", response_model=ReviewSchedule, status_code=201)
def create_schedule(selection: ScheduleInput) -> ReviewSchedule:
    _check_enabled(selection)
    return _store().save(selection, datetime.now(timezone.utc))


@router.put("/{schedule_id}", response_model=ReviewSchedule)
def update_schedule(schedule_id: UUID, selection: ScheduleUpdate) -> ReviewSchedule:
    _check_enabled(selection)
    return _store().save(ScheduleInput.model_validate(selection.model_dump(exclude={"revision"})),
                         datetime.now(timezone.utc), schedule_id, selection.revision)


@router.delete("/{schedule_id}", response_model=dict[str, bool])
def delete_schedule(schedule_id: UUID, revision: Annotated[int, Query(ge=1)]) -> dict[str, bool]:
    _store().delete(schedule_id, revision)
    return {"deleted": True}
