from datetime import datetime, timezone
import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pyodbc
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from app.auth import require_user
from app import recognition_repository as repository
from app.inspection import (
    MAX_BURST_FRAME_BYTES, MAX_UPLOAD_BYTES, InvalidScreenshot, analyze_burst, analyze_screenshot,
)
from app.recognition_models import (
    Baseline, RecognitionPage, RecognitionRecord, Region, ReviewStatus, ReviewUpdate,
)
from app.vision_analysis import AnalysisError

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
    prefix="/api/recognitions", tags=["recognitions"], dependencies=[Depends(require_user), Depends(database_errors)],
)


@router.post("", response_model=RecognitionRecord, status_code=201)
def upload_screenshot(
    screenshot: Annotated[UploadFile, File()],
    region: Annotated[Region, Form()],
    shop_code: Annotated[str, Form(max_length=50, description="Optional shop code; blank when omitted.")] = "",
    camera_name: Annotated[str, Form(max_length=100, description="Optional camera name; blank when omitted.")] = "",
    captured_at: Annotated[datetime | None, Form()] = None,
    burst_frames: Annotated[list[UploadFile] | None, File()] = None,
    burst_duration_seconds: Annotated[float | None, Form(ge=1.5, le=2.0)] = None,
) -> RecognitionRecord:
    shop_code, camera_name = shop_code.strip(), camera_name.strip()
    if captured_at is not None and captured_at.utcoffset() is None:
        raise HTTPException(422, "Capture time must include a timezone offset.")
    try:
        content = screenshot.file.read(MAX_UPLOAD_BYTES + 1)
        if burst_frames:
            if burst_duration_seconds is None:
                raise InvalidScreenshot("A burst duration between 1.5 and 2 seconds is required.")
            if not 5 <= len(burst_frames) <= 9:
                raise InvalidScreenshot("A burst requires 6 to 10 frames including the screenshot.")
            frames = [frame.file.read(MAX_BURST_FRAME_BYTES + 1) for frame in burst_frames]
            baseline = repository.get_baseline_evidence(region, shop_code, camera_name) if shop_code and camera_name else None
            analysis, saved_frames = analyze_burst(content, frames, captured_at, baseline, burst_duration_seconds)
        else:
            if burst_duration_seconds is not None:
                raise InvalidScreenshot("Burst duration requires additional burst frames.")
            baseline = repository.get_baseline_evidence(region, shop_code, camera_name) if shop_code and camera_name else None
            analysis = analyze_screenshot(content, captured_at, baseline)
    except InvalidScreenshot as error:
        raise HTTPException(422, str(error)) from None
    except AnalysisError:
        raise HTTPException(502, "Vision analysis unavailable or invalid; the screenshot was not saved.") from None
    finally:
        screenshot.file.close()
        for frame in burst_frames or []:
            frame.file.close()
    filename = (screenshot.filename or "screenshot").replace("\\", "/").rsplit("/", 1)[-1]
    record = RecognitionRecord(
        id=uuid4(), shop_code=shop_code, camera_name=camera_name,
        region=region, filename=filename[:255] or "screenshot",
        uploaded_at=datetime.now(timezone.utc), captured_at=captured_at,
        width=analysis.width, height=analysis.height,
        outcome="flagged" if any(c.status == "flagged" for c in analysis.checks) else "needs_review",
        review_status="new", review_note="", reviewed_at=None,
        analyzer_version=analysis.analyzer_version, checks=analysis.checks,
    )
    if burst_frames:
        repository.save_record(record, analysis.evidence, saved_frames, burst_duration_seconds)
    else:
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


@router.get("/{record_id}/burst")
def recognition_burst_info(record_id: UUID) -> dict[str, int | float]:
    result = repository.get_burst_info(record_id)
    if result is None:
        raise HTTPException(404, "Burst evidence not found.")
    count, duration = result
    return {"frame_count": count, "duration_seconds": duration}


@router.get("/{record_id}/burst/{frame_index}")
def recognition_burst_frame(record_id: UUID, frame_index: Annotated[int, Path(ge=1, le=9)]) -> Response:
    evidence = repository.get_burst_frame(record_id, frame_index)
    if evidence is None:
        raise HTTPException(404, "Burst frame not found.")
    return Response(
        content=evidence, media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{record_id}/baseline", response_model=Baseline)
def recognition_baseline(record_id: UUID) -> Baseline:
    baseline = repository.get_baseline(record_id)
    if baseline is None:
        raise HTTPException(404, "Approved baseline not found.")
    return baseline


@router.post("/{record_id}/baseline", response_model=Baseline, status_code=201)
def approve_recognition_baseline(record_id: UUID) -> Baseline:
    record = repository.get_record(record_id)
    if record is None:
        raise HTTPException(404, "Recognition record not found.")
    if not record.shop_code.strip() or not record.camera_name.strip():
        raise HTTPException(422, "Shop code and camera name are required for a camera baseline, not for uploads.")
    baseline = repository.approve_baseline(record_id)
    if baseline is None:
        raise HTTPException(404, "Recognition record not found.")
    return baseline


@router.patch("/{record_id}/review", response_model=RecognitionRecord)
def recognition_review(record_id: UUID, update: ReviewUpdate) -> RecognitionRecord:
    record = repository.update_review(record_id, update)
    if record is None:
        raise HTTPException(404, "Recognition record not found.")
    return record
