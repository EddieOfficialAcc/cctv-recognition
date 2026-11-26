"""On-demand, capture-only batches, independent of the daily SQL inspection runs.

Reports live at <snapshot_dir>/batches/<batch UUID>/report.json; JPEGs are below
that folder, grouped by Hong Kong date and recorder index. A SQL session lock
serializes backend instances and permits interrupted-job recovery after restart.
"""
from contextlib import closing, contextmanager
from datetime import datetime
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
import time as clock
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pyodbc
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

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

logger = logging.getLogger(__name__)


class BatchRun(Run):
    id: UUID
    error: str = ""


class BatchReport(BaseModel):
    id: UUID
    status: Literal["running", "completed", "interrupted"] = "running"
    started_at: datetime
    finished_at: datetime | None = None
    error: str = ""
    items: list[Item]
    recorder_keys: dict[int, str] = Field(default_factory=dict)
    recheck_skipped: bool = False

    def summary(self) -> BatchRun:
        counts = {status: sum(item.status == status for item in self.items)
                  for status in ("completed", "failed", "skipped", "interrupted")}
        return BatchRun(
            id=self.id, run_date=self.started_at.astimezone(HONG_KONG).date(),
            status=self.status, started_at=self.started_at, finished_at=self.finished_at,
            total=len(self.items), saved=sum(item.has_evidence for item in self.items), error=self.error, **counts,
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
    except (OSError, ValueError, pyodbc.Error, SnapshotStorageError) as error:
        logger.error("Snapshot batch storage operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "Snapshot batch storage unavailable.") from None


def storage_errors(response: Response):
    response.headers["Cache-Control"] = "no-store"
    with _storage_errors():
        yield


def _plan_batch(password: str, root: Path | None = None,
                recheck_skipped: bool = False) -> tuple[BatchReport, dict[int, Recorder]]:
    root = root if root is not None else RecorderSettings().snapshot_dir
    exclusions = ChannelExclusions(root)
    report = BatchReport(id=uuid4(), started_at=_utc_now(), items=[], recheck_skipped=recheck_skipped)
    recorders: dict[int, Recorder] = {}
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(_eligible_recorders(include_missing_addresses=True), 1):
        shop, region, address, quantity = row
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
            recorder = Recorder(address, "admin", password)
            key = (shop, region, recorder.base)
            if key in seen:
                raise RecorderError("Duplicate recorder configuration for this shop and address.")
            seen.add(key)
            recorders[index] = recorder
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


def _failure_reason(error: Exception) -> str:
    if isinstance(error, OSError):
        windows_code = getattr(error, "winerror", None)
        code = f"Windows error {windows_code}" if windows_code is not None else f"OS error {error.errno}"
        return (f"Local archive/report operation failed ({type(error).__name__}, {code}). "
                "Check archive permissions, free disk space and file locks.")
    if isinstance(error, pyodbc.Error):
        return "The batch database connection failed. Check database connectivity and backend logs."
    return f"Unexpected batch processing error ({type(error).__name__}). Check backend logs."


def _capture_batch(root: Path, report: BatchReport, recorders: dict[int, Recorder], connection) -> None:
    try:
        exclusions = ChannelExclusions(root)
        present: dict[int, set[int]] = {}
        not_found: dict[int, set[int]] = {}
        rejected: set[int] = set()
        for item in report.items:
            if item.status != "pending":
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
        for index, key in report.recorder_keys.items():
            exclusions.record_results(
                key, present.get(index, set()), not_found.get(index, set()), recheck=report.recheck_skipped,
            )
        report.status = "completed"
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
def start_batch(background: BackgroundTasks, recheck_skipped: bool = False) -> BatchRun:
    settings = RecorderSettings()
    password = settings.dahau_password.get_secret_value()
    if not password.strip():
        raise HTTPException(503, "Manual snapshot capture is not configured.")
    connection = _acquire_lock()
    if connection is None:
        raise HTTPException(409, "A manual snapshot batch is already running.")
    try:
        _recover_interrupted(settings.snapshot_dir)
        report, recorders = _plan_batch(password, settings.snapshot_dir, recheck_skipped)
        if not report.items:
            raise HTTPException(422, "No DAHAU recorders are configured for batch capture.")
        _write_report(settings.snapshot_dir, report)
        background.add_task(_capture_batch, settings.snapshot_dir, report, recorders, connection)
        return report.summary()
    except Exception:
        _release_lock(connection)
        raise


@router.get("", response_model=list[BatchRun])
def list_batches() -> list[BatchRun]:
    root = RecorderSettings().snapshot_dir
    _refresh_reports(root)
    reports = sorted(_reports(root), key=lambda report: report.started_at, reverse=True)
    return [report.summary() for report in reports[:100]]


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
        shop, region, address, quantity = candidates[0]
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
