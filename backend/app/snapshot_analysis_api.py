from datetime import date
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError

from app.auth import require_user
from app.recorder import RecorderSettings
from app.snapshot_analysis import AnalysisPage, AnalysisQueue, AnalysisStatus, QueueError, QueueSettings
from app.vision_analysis import VisionSettings

logger = logging.getLogger(__name__)


def vision_configured() -> bool:
    try:
        return bool(VisionSettings().api_key.get_secret_value().strip())
    except ValidationError:
        return False


def require_vision() -> None:
    if not vision_configured():
        raise HTTPException(503, "Vision analysis is not configured.")


def queue_errors(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        yield
    except (QueueError, ValidationError) as error:
        logger.error("AI review operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "AI review storage or configuration unavailable.") from None


class AnalysisSettings(BaseModel):
    auto_analyze: bool
    configured: bool
    worker_enabled: bool
    worker_online: bool
    concurrency: int


class AutoAnalysisUpdate(BaseModel):
    auto_analyze: bool = Field(strict=True)


router = APIRouter(prefix="/api/snapshot-analysis", tags=["snapshot analysis"],
                   dependencies=[Depends(require_user), Depends(queue_errors)])


@router.get("/settings", response_model=AnalysisSettings)
def settings() -> AnalysisSettings:
    queue = AnalysisQueue(RecorderSettings().snapshot_dir)
    state = queue.page("").summary
    worker = QueueSettings()
    return AnalysisSettings(auto_analyze=state.auto_analyze, configured=vision_configured(),
                            worker_enabled=worker.worker_enabled, worker_online=state.worker_online,
                            concurrency=worker.concurrency)


@router.patch("/settings", response_model=AnalysisSettings)
def update_settings(update: AutoAnalysisUpdate) -> AnalysisSettings:
    if update.auto_analyze:
        require_vision()
    AnalysisQueue(RecorderSettings().snapshot_dir).set_auto_analyze(update.auto_analyze)
    return settings()


def _source(kind: Literal["batch", "scheduled"], source_id: str) -> str:
    try:
        identifier = str(UUID(source_id)) if kind == "batch" else date.fromisoformat(source_id).isoformat()
    except ValueError:
        raise HTTPException(422, "Invalid AI review source.") from None
    return f"{kind}:{identifier}"


@router.get("/{kind}/{source_id}", response_model=AnalysisPage)
def analysis_page(kind: Literal["batch", "scheduled"], source_id: str,
                  page: Annotated[int, Query(ge=1)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=100)] = 20,
                  status: AnalysisStatus | None = None) -> AnalysisPage:
    return AnalysisQueue(RecorderSettings().snapshot_dir).page(_source(kind, source_id), page, page_size, status)


@router.post("/{kind}/{source_id}/retry", response_model=AnalysisPage, status_code=202)
def retry_analysis(kind: Literal["batch", "scheduled"], source_id: str) -> AnalysisPage:
    require_vision()
    queue = AnalysisQueue(RecorderSettings().snapshot_dir)
    source = _source(kind, source_id)
    queue.retry_failed(source)
    return queue.page(source)


@router.post("/{kind}/{source_id}/stop", response_model=AnalysisPage, status_code=202)
def stop_analysis(kind: Literal["batch", "scheduled"], source_id: str) -> AnalysisPage:
    queue = AnalysisQueue(RecorderSettings().snapshot_dir)
    source = _source(kind, source_id)
    queue.stop_source(source)
    return queue.page(source)


@router.post("/{kind}/{source_id}/resume", response_model=AnalysisPage, status_code=202)
def resume_analysis(kind: Literal["batch", "scheduled"], source_id: str) -> AnalysisPage:
    require_vision()
    queue = AnalysisQueue(RecorderSettings().snapshot_dir)
    source = _source(kind, source_id)
    queue.resume_source(source)
    return queue.page(source)


@router.get("/{kind}/{source_id}/report")
def analysis_report(kind: Literal["batch", "scheduled"], source_id: str) -> Response:
    source = _source(kind, source_id)
    queue = AnalysisQueue(RecorderSettings().snapshot_dir)
    total = queue.page(source, page_size=1).summary.total
    report = queue.page(source, page_size=max(1, total))
    return Response(
        report.model_dump_json(indent=2), media_type="application/json",
        headers={"Cache-Control": "no-store",
                 "Content-Disposition": f'attachment; filename="ai-review-{source.replace(":", "-")}.json"'},
    )
