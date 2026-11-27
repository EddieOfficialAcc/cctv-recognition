"""On-demand capture batches, independent of the daily SQL inspection runs.

Reports live at <snapshot_dir>/batches/<batch UUID>/report.json; JPEGs are below
that folder, grouped by Hong Kong date and recorder index. A SQL session lock
serializes backend instances and permits interrupted-job recovery after restart.
"""
from contextlib import closing, contextmanager
from datetime import datetime
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
import time as clock
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pyodbc
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError

from app.auth import require_user
from app.channel_exclusions import ChannelExclusions, exclusion_message, recorder_key
from app.database import DatabaseSettings, connect
from app.inspection import InvalidScreenshot, prepare_screenshot
from app.inspection_reports import Item, ItemPage, Run
from app.recorder import (
    MAX_CHANNELS, Recorder, RecorderAuthenticationError, RecorderChannelNotFoundError,
    RecorderError, RecorderSettings, _address,
)
from app.scheduled_inspection import HONG_KONG, _eligible_recorders, _utc_now
from app.snapshot_storage import SnapshotStorageError, save_snapshot
from app.snapshot_analysis import AnalysisPage, AnalysisQueue, QueueError
from app.snapshot_analysis_api import require_vision

logger = logging.getLogger(__name__)


class BatchRun(Run):
    id: UUID
    error: str = ""
    analysis_error: str = ""
    schedule_name: str = ""
    stopped: int = 0
    capture_brands: list[Literal["DAHAU", "HIKVISION"]] = Field(default_factory=list)
    stop_requested: list[Literal["DAHAU", "HIKVISION"]] = Field(default_factory=list)


class BatchReport(BaseModel):
    id: UUID
    status: Literal["running", "completed", "interrupted", "stopped"] = "running"
    started_at: datetime
    finished_at: datetime | None = None
    error: str = ""
    items: list[Item]
    recorder_keys: dict[int, str] = Field(default_factory=dict)
    recorder_brands: dict[int, Literal["DAHAU", "HIKVISION"]] = Field(default_factory=dict)
    recheck_skipped: bool = False
    auto_analyze: bool = False
    analysis_error: str = ""
    schedule_name: str = ""

    def summary(self) -> BatchRun:
        counts = {status: sum(item.status == status for item in self.items)
                  for status in ("completed", "failed", "skipped", "interrupted", "stopped")}
        brands = sorted({self.recorder_brands[item.recorder_index] for item in self.items
                         if item.status in ("pending", "capturing") and item.recorder_index in self.recorder_brands})
        return BatchRun(
            id=self.id, run_date=self.started_at.astimezone(HONG_KONG).date(),
            status=self.status, started_at=self.started_at, finished_at=self.finished_at,
            total=len(self.items), saved=sum(item.has_evidence for item in self.items),
            error=self.error, analysis_error=self.analysis_error, schedule_name=self.schedule_name,
            capture_brands=brands, **counts,
        )


def _directory(root: Path, batch_id: UUID) -> Path:
    return root / "batches" / str(batch_id)


def _write_report(root: Path, report: BatchReport) -> None:
    directory = _directory(root, report.id)
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(report.model_dump_json())
        for attempt in range(6):
            try:
                temporary.replace(directory / "report.json")
                break
            except PermissionError as error:
                # Windows readers may briefly deny replacing an existing report.
                if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 5:
                    raise
                clock.sleep(0.02 * 2 ** attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_report(root: Path, batch_id: UUID) -> BatchReport:
    try:
        return BatchReport.model_validate_json((_directory(root, batch_id) / "report.json").read_text("utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, "Snapshot batch not found.") from None


def _reports(root: Path) -> list[BatchReport]:
    directory = root / "batches"
    if not directory.exists():
        return []
    return [_read_report(root, UUID(path.parent.name)) for path in directory.glob("*/report.json")]


def _acquire_lock():
    connection = connect(DatabaseSettings())
    try:
        with closing(connection.cursor()) as cursor:
            result = cursor.execute(
                "SET NOCOUNT ON; DECLARE @result int; EXEC @result = sp_getapplock "
                "@Resource='cctv_manual_snapshot_batch', @LockMode='Exclusive', "
                "@LockOwner='Session', @LockTimeout=0; SELECT @result"
            ).fetchone()[0]
        if result < 0:
            connection.close()
            return None
        return connection
    except Exception:
        connection.close()
        raise


def _release_lock(connection) -> None:
    try:
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                "EXEC sp_releaseapplock @Resource='cctv_manual_snapshot_batch', @LockOwner='Session'"
            )
    finally:
        connection.close()


def _recover_interrupted(root: Path) -> None:
    # Call only while holding the batch lock: another backend may own the active job.
    for report in _reports(root):
        if report.status != "running":
            continue
        for item in report.items:
            if item.status in ("pending", "capturing"):
                item.status = "interrupted"
                item.message = "Backend stopped before completing this channel. Start a new batch to retry."
        report.status = "interrupted"
        report.error = "Backend stopped before the batch finished."
        report.finished_at = _utc_now()
        _write_report(root, report)


def _refresh_reports(root: Path) -> None:
    connection = _acquire_lock()
    if connection is not None:
        try:
            _recover_interrupted(root)
        finally:
            _release_lock(connection)


@contextmanager
def _storage_errors():
    try:
        yield
    except QueueError as error:
        logger.error("Snapshot AI queue operation failed: %s", error)
        raise HTTPException(503, "AI review storage or configuration unavailable.") from None
    except (OSError, ValueError, pyodbc.Error, SnapshotStorageError) as error:
        logger.error("Snapshot batch storage operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "Snapshot batch storage unavailable.") from None


def storage_errors(response: Response):
    response.headers["Cache-Control"] = "no-store"
    with _storage_errors():
        yield


def _plan_batch(password: str, root: Path | None = None,
                recheck_skipped: bool = False, hikvision_password: str = "",
                *, brand_filter: Literal["DAHAU", "HIKVISION"] | None = None) -> tuple[BatchReport, dict[int, Recorder]]:
    root = root if root is not None else RecorderSettings().snapshot_dir
    exclusions = ChannelExclusions(root)
    report = BatchReport(id=uuid4(), started_at=_utc_now(), items=[], recheck_skipped=recheck_skipped)
    recorders: dict[int, Recorder] = {}
    seen: set[tuple[str, str, str]] = set()
    rows = (_eligible_recorders(include_missing_addresses=True, brand=brand_filter)
            if brand_filter else _eligible_recorders(include_missing_addresses=True))
    for index, row in enumerate(rows, 1):
        if len(row) >= 7:
            shop, region, address, quantity, brand, username, db_password = row[:7]
        else:
            shop, region, address, quantity = row[:4]
            brand, username, db_password = "DAHAU", "admin", ""
        shop, region = (shop or "").strip(), (region or "").strip()
        message = ""
        excluded: dict[int, str] = {}
        try:
            if not shop or len(shop) > 50 or region not in ("Hong Kong", "Macau"):
                raise RecorderError("Shop code or region is invalid.")
            if not isinstance(quantity, int) or not 1 <= quantity <= MAX_CHANNELS:
                raise RecorderError(f"Configured camera count must be between 1 and {MAX_CHANNELS}.")
            if not isinstance(address, str) or not address.strip():
                raise RecorderError("Recorder internal IP address is missing.")
            brand = (brand or "DAHAU").strip().upper()
            username = (username or "admin").strip()
            recorder_password = (db_password or "").strip()
            if not recorder_password:
                recorder_password = hikvision_password if brand == "HIKVISION" else password
            recorder = (Recorder(address, username, recorder_password, brand)
                        if brand != "DAHAU" else Recorder(address, username, recorder_password))
            key = (shop, region, recorder.base)
            if key in seen:
                raise RecorderError("Duplicate recorder configuration for this shop and address.")
            seen.add(key)
            recorders[index] = recorder
            report.recorder_brands[index] = "HIKVISION" if recorder.brand == "HIKVISION" else "DAHAU"
            report.recorder_keys[index] = recorder_key(shop, region, recorder.base)
            if not recheck_skipped:
                excluded = exclusions.get(report.recorder_keys[index])
        except RecorderError as error:
            message = str(error)
        channels = range(1, quantity + 1) if not message else (0,)
        for channel in channels:
            skipped_channel = channel in excluded
            report.items.append(Item(
                id=uuid4(), recorder_index=index, shop_code=shop[:50], region=region[:20],
                channel=channel, camera_name=f"Channel {channel}" if channel else "",
                status="skipped" if message or skipped_channel else "pending",
                coverage_method="none" if message else "excluded" if skipped_channel else "configured_count",
                message=exclusion_message(excluded[channel]) if skipped_channel else message,
                captured_at=None, has_evidence=False, has_record=False,
                excluded=skipped_channel, can_exclude=not bool(message),
            ))
    return report, recorders


def _evidence_path(root: Path, report: BatchReport, item: Item) -> Path:
    return (_directory(root, report.id) / report.started_at.astimezone(HONG_KONG).date().isoformat()
            / str(item.recorder_index) / f"channel-{item.channel:03d}.jpeg")


def index_capture_history(queue: AnalysisQueue) -> None:
    try:
        indexed = queue.capture_ids()
        for report in _reports(queue.root):
            for item in report.items:
                key = report.recorder_keys.get(item.recorder_index)
                if (str(item.id) not in indexed and item.status == "completed"
                        and item.has_evidence and item.captured_at and key):
                    queue.register_capture(item.id, _evidence_path(queue.root, report, item), key,
                                           item.channel, item.captured_at, historical=True)
                    indexed.add(str(item.id))
    except (OSError, ValueError, ValidationError, HTTPException) as error:
        logger.error("Snapshot history indexing failed (%s).", type(error).__name__)
        raise QueueError("Snapshot history could not be indexed.") from None


class CaptureStoppedError(RuntimeError):
    pass


def _failure_reason(error: Exception) -> str:
    if isinstance(error, CaptureStoppedError):
        return "Backend stopped before scheduled capture completed. Saved snapshots and AI jobs are retained."
    if isinstance(error, OSError):
        windows_code = getattr(error, "winerror", None)
        code = f"Windows error {windows_code}" if windows_code is not None else f"OS error {error.errno}"
        return (f"Local archive/report operation failed ({type(error).__name__}, {code}). "
                "Check archive permissions, free disk space and file locks.")
    if isinstance(error, pyodbc.Error):
        return "The batch database connection failed. Check database connectivity and backend logs."
    return f"Unexpected batch processing error ({type(error).__name__}). Check backend logs."


def _capture_batch(root: Path, report: BatchReport, recorders: dict[int, Recorder], connection,
                   *, stopped: Event | None = None) -> None:
    try:
        exclusions = ChannelExclusions(root)
        queue = AnalysisQueue(root)
        present: dict[int, set[int]] = {}
        not_found: dict[int, set[int]] = {}
        rejected: set[int] = set()
        applied_stops: set[str] = set()
        for item in report.items:
            if item.status != "pending":
                continue
            if stopped is not None and stopped.is_set():
                raise CaptureStoppedError()
            stop_brands = queue.capture_stops_at_boundary(report.id) - applied_stops
            changed = False
            if stop_brands:
                for pending in report.items:
                    if pending.status != "pending":
                        continue
                    brand = report.recorder_brands.get(pending.recorder_index, recorders[pending.recorder_index].brand)
                    if brand in stop_brands:
                        pending.status = "stopped"
                        pending.message = "Capture stopped by user. Start a new batch to capture this channel."
                        changed = True
                applied_stops.update(stop_brands)
            if changed:
                _write_report(root, report)
            if item.status == "stopped":
                continue
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT 1").fetchone()
            if item.recorder_index in rejected:
                item.status = "failed"
                item.message = "Not attempted: recorder authentication failed on a previous channel."
            else:
                item.status = "capturing"
                _write_report(root, report)
                try:
                    image = prepare_screenshot(recorders[item.recorder_index].snapshot(item.channel))
                    present.setdefault(item.recorder_index, set()).add(item.channel)
                    item.captured_at = _utc_now()
                    save_snapshot(_directory(root, report.id), report.started_at.astimezone(HONG_KONG).date(),
                                  str(item.recorder_index), item.channel, image)
                    item.has_evidence = True
                    item.status = "completed"
                except (RecorderError, InvalidScreenshot, SnapshotStorageError) as error:
                    if isinstance(error, RecorderAuthenticationError):
                        rejected.add(item.recorder_index)
                    if isinstance(error, RecorderChannelNotFoundError):
                        not_found.setdefault(item.recorder_index, set()).add(item.channel)
                    item.status = "failed"
                    item.message = str(error)[:1000]
            _write_report(root, report)
            if item.has_evidence and item.captured_at:
                queue.register_capture(
                    item.id, _evidence_path(root, report, item),
                    report.recorder_keys[item.recorder_index], item.channel, item.captured_at,
                )
            if report.auto_analyze and item.has_evidence:
                try:
                    _enqueue_item(root, report, item)
                except QueueError as error:
                    report.analysis_error = str(error)
                    logger.error("Snapshot saved but AI enqueue failed for %s: %s", item.id, error)
                    _write_report(root, report)
        for index, key in report.recorder_keys.items():
            exclusions.record_results(
                key, present.get(index, set()), not_found.get(index, set()), recheck=report.recheck_skipped,
            )
        report.status = "stopped" if any(item.status == "stopped" for item in report.items) else "completed"
        report.finished_at = _utc_now()
        _write_report(root, report)
    except Exception as error:
        report.error = _failure_reason(error)
        logger.error("Snapshot batch %s stopped: %s", report.id, report.error)
        report.status = "interrupted"
        report.finished_at = _utc_now()
        for item in report.items:
            if item.status in ("pending", "capturing"):
                item.status = "interrupted"
                item.message = f"{report.error} Start a new batch to retry."
        try:
            _write_report(root, report)
        except OSError as storage_error:
            logger.error("Could not persist interrupted batch (%s).", type(storage_error).__name__)
    finally:
        _release_lock(connection)


router = APIRouter(prefix="/api/snapshot-batches", tags=["snapshot batches"],
                   dependencies=[Depends(require_user), Depends(storage_errors)])


@router.post("", response_model=BatchRun, status_code=202)
def start_batch(background: BackgroundTasks, recheck_skipped: bool = False,
                brand: Literal["DAHAU", "HIKVISION"] | None = None) -> BatchRun:
    settings = RecorderSettings()
    password = settings.dahau_password.get_secret_value()
    hikvision_password = settings.hikvision_password.get_secret_value()
    if not password.strip() and not hikvision_password.strip():
        raise HTTPException(503, "Manual snapshot capture is not configured.")
    auto_analyze = AnalysisQueue(settings.snapshot_dir).auto_analyze()
    connection = _acquire_lock()
    if connection is None:
        raise HTTPException(409, "A manual snapshot batch is already running.")
    try:
        _recover_interrupted(settings.snapshot_dir)
        report, recorders = _plan_batch(
            password, settings.snapshot_dir, recheck_skipped, hikvision_password, brand_filter=brand,
        )
        report.auto_analyze = auto_analyze
        if not report.items:
            raise HTTPException(422, "No recorders match the selected brand for batch capture.")
        _write_report(settings.snapshot_dir, report)
        background.add_task(_capture_batch, settings.snapshot_dir, report, recorders, connection)
        return report.summary()
    except Exception:
        _release_lock(connection)
        raise


@router.post("/{batch_id}/stop/{brand}", response_model=BatchRun, status_code=202)
def stop_batch_brand(batch_id: UUID, brand: Literal["DAHAU", "HIKVISION"]) -> BatchRun:
    root = RecorderSettings().snapshot_dir
    report = _read_report(root, batch_id)
    summary = report.summary()
    if report.status != "running" or brand not in summary.capture_brands:
        raise HTTPException(409, "This brand has no capture in progress.")
    queue = AnalysisQueue(root)
    queue.request_capture_stop(batch_id, brand)
    stops = queue.capture_stops(batch_id)
    summary.stop_requested = [value for value in ("DAHAU", "HIKVISION") if value in stops]
    return summary


def _enqueue_item(root: Path, report: BatchReport, item: Item) -> None:
    AnalysisQueue(root).enqueue(
        f"batch:{report.id}", item.id, _evidence_path(root, report, item), item.shop_code, item.region,
        item.channel, item.captured_at, recorder_key=report.recorder_keys.get(item.recorder_index),
    )


class AnalysisSelection(BaseModel):
    limit: Literal[20] | None = 20


@router.post("/{batch_id}/analysis", response_model=AnalysisPage, status_code=202)
def analyze_batch(batch_id: UUID, selection: AnalysisSelection) -> AnalysisPage:
    require_vision()
    root = RecorderSettings().snapshot_dir
    report = _read_report(root, batch_id)
    if report.status == "running":
        raise HTTPException(409, "Wait for capture to finish before starting a batch review.")
    items = [item for item in report.items if item.has_evidence]
    if selection.limit:
        items = items[:selection.limit]
    if not items:
        raise HTTPException(422, "This batch has no saved snapshots to analyze.")
    for item in items:
        _enqueue_item(root, report, item)
    return AnalysisQueue(root).page(f"batch:{batch_id}")


@router.get("", response_model=list[BatchRun])
def list_batches() -> list[BatchRun]:
    root = RecorderSettings().snapshot_dir
    _refresh_reports(root)
    reports = sorted(_reports(root), key=lambda report: report.started_at, reverse=True)
    summaries = []
    for report in reports[:100]:
        summary = report.summary()
        if report.status == "running":
            stops = AnalysisQueue(root).capture_stops(report.id)
            summary.stop_requested = [value for value in ("DAHAU", "HIKVISION") if value in stops]
        summaries.append(summary)
    return summaries


@router.get("/{batch_id}/items", response_model=ItemPage)
def batch_items(batch_id: UUID, page: Annotated[int, Query(ge=1)] = 1,
                page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> ItemPage:
    root = RecorderSettings().snapshot_dir
    _refresh_reports(root)
    report = _read_report(root, batch_id)
    start = (page - 1) * page_size
    keys = _report_recorder_keys(report)
    exclusions = ChannelExclusions(root)
    excluded = {index: exclusions.get(key) for index, key in keys.items()}
    items = [item.model_copy(update={
        "can_exclude": item.channel > 0 and item.recorder_index in keys,
        "excluded": item.channel in excluded.get(item.recorder_index, {}),
    }) for item in report.items[start:start + page_size]]
    return ItemPage(items=items, total=len(report.items),
                    page=page, page_size=page_size)


def _report_recorder_keys(report: BatchReport) -> dict[int, str]:
    keys = dict(report.recorder_keys)
    unresolved = {item.recorder_index: item for item in report.items
                  if item.channel > 0 and item.recorder_index not in keys}
    if not unresolved:
        return keys
    rows = _eligible_recorders(include_missing_addresses=True)
    for index, item in unresolved.items():
        candidates = [row for row in rows
                      if (row[0] or "").strip() == item.shop_code and (row[1] or "").strip() == item.region]
        if len(candidates) != 1:
            continue
        shop, region, address, quantity = candidates[0][:4]
        if not isinstance(address, str) or not address.strip() or not isinstance(quantity, int):
            continue
        if not 1 <= item.channel <= quantity <= MAX_CHANNELS:
            continue
        try:
            keys[index] = recorder_key(shop.strip(), region.strip(), f"http://{_address(address)}")
        except RecorderError:
            continue
    return keys


class ChannelExclusionRequest(BaseModel):
    skip: bool = Field(strict=True)


@router.patch("/{batch_id}/items/{item_id}/exclusion", response_model=ChannelExclusionRequest)
def set_channel_exclusion(batch_id: UUID, item_id: UUID,
                          selection: ChannelExclusionRequest) -> ChannelExclusionRequest:
    root = RecorderSettings().snapshot_dir
    report = _read_report(root, batch_id)
    item = next((entry for entry in report.items if entry.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Snapshot batch item not found.")
    key = _report_recorder_keys(report).get(item.recorder_index)
    if item.channel < 1 or key is None:
        raise HTTPException(409, "A unique current recorder configuration is required to exclude this channel.")
    ChannelExclusions(root).set_skipped(key, item.channel, selection.skip)
    return selection


@router.get("/{batch_id}/report")
def download_report(batch_id: UUID) -> Response:
    root = RecorderSettings().snapshot_dir
    _refresh_reports(root)
    report = _read_report(root, batch_id)
    return Response(content=report.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="snapshot-batch-{batch_id}.json"',
                             "Cache-Control": "no-store"})


@router.get("/{batch_id}/items/{item_id}/evidence")
def batch_evidence(batch_id: UUID, item_id: UUID) -> FileResponse:
    root = RecorderSettings().snapshot_dir
    report = _read_report(root, batch_id)
    item = next((item for item in report.items if item.id == item_id), None)
    if item is None or not item.has_evidence:
        raise HTTPException(404, "Snapshot batch evidence not found.")
    path = _evidence_path(root, report, item)
    if not path.is_file():
        raise HTTPException(404, "Snapshot batch evidence file is missing.")
    return FileResponse(path, media_type="image/jpeg", filename=f"snapshot-{item_id}.jpeg",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
