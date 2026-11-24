from datetime import datetime, timezone
import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pyodbc
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from app import recognition_repository as repository
from app.inspection import ANALYZER_VERSION, MAX_UPLOAD_BYTES, InvalidScreenshot, analyze_screenshot
from app.recognition_models import (
    RecognitionPage, RecognitionRecord, Region, ReviewStatus, ReviewUpdate,
)

logger = logging.getLogger(__name__)


def database_errors():
    try:
        yield
    except (pyodbc.Error, ValidationError) as error:
        logger.error("Recognition database operation failed (%s).", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="Recognition storage unavailable. Check database configuration and schema setup.",
        ) from None


router = APIRouter(
    prefix="/api/recognitions", tags=["recognitions"], dependencies=[Depends(database_errors)],
)


@router.post("", response_model=RecognitionRecord, status_code=201)
def upload_screenshot(
    screenshot: Annotated[UploadFile, File()],
    shop_code: Annotated[str, Form(min_length=1, max_length=50)],
    camera_name: Annotated[str, Form(min_length=1, max_length=100)],
    region: Annotated[Region, Form()],
    captured_at: Annotated[datetime | None, Form()] = None,
) -> RecognitionRecord:
    if not shop_code.strip() or not camera_name.strip():
        raise HTTPException(422, "Shop code and camera name cannot be blank.")
    if captured_at is not None and captured_at.utcoffset() is None:
        raise HTTPException(422, "Capture time must include a timezone offset.")
    try:
        content = screenshot.file.read(MAX_UPLOAD_BYTES + 1)
        analysis = analyze_screenshot(content)
    except InvalidScreenshot as error:
        raise HTTPException(422, str(error)) from None
    finally:
        screenshot.file.close()
    filename = (screenshot.filename or "screenshot").replace("\\", "/").rsplit("/", 1)[-1]
    record = RecognitionRecord(
        id=uuid4(), shop_code=shop_code.strip(), camera_name=camera_name.strip(),
        region=region, filename=filename[:255] or "screenshot",
        uploaded_at=datetime.now(timezone.utc), captured_at=captured_at,
        width=analysis.width, height=analysis.height,
        outcome="flagged" if any(c.status == "flagged" for c in analysis.checks) else "needs_review",
        review_status="new", review_note="", reviewed_at=None,
        analyzer_version=ANALYZER_VERSION, checks=analysis.checks,
    )
    repository.save_record(record, analysis.evidence)
    return record


@router.get("", response_model=RecognitionPage)
def recognition_list(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str, Query(max_length=100)] = "",
    outcome: Literal["flagged", "needs_review"] | None = None,
    review_status: ReviewStatus | None = None,
    region: Region | None = None,
) -> RecognitionPage:
    return repository.list_records(page, page_size, search.strip(), outcome, review_status, region)


@router.get("/{record_id}", response_model=RecognitionRecord)
def recognition_detail(record_id: UUID) -> RecognitionRecord:
    record = repository.get_record(record_id)
    if record is None:
        raise HTTPException(404, "Recognition record not found.")
    return record


@router.get("/{record_id}/evidence")
def recognition_evidence(record_id: UUID) -> Response:
    evidence = repository.get_evidence(record_id)
    if evidence is None:
        raise HTTPException(404, "Recognition evidence not found.")
    return Response(
        content=evidence, media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.patch("/{record_id}/review", response_model=RecognitionRecord)
def recognition_review(record_id: UUID, update: ReviewUpdate) -> RecognitionRecord:
    record = repository.update_review(record_id, update)
    if record is None:
        raise HTTPException(404, "Recognition record not found.")
    return record
