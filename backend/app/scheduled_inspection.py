"""Legacy daily-report helpers and optional frontend-managed scheduler CLI.

The backend starts the shared schedule engine automatically. This CLI uses the
same leases and settings; it no longer creates a separate fixed daily trigger.
"""
from collections.abc import Container
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import os
from pathlib import Path
import time as clock
from uuid import UUID, uuid4

import pyodbc
from pydantic import ValidationError

from app.database import DatabaseSettings, connect
from app.channel_exclusions import ChannelExclusions, exclusion_message, recorder_key
from app.inspection import InvalidScreenshot, prepare_screenshot
from app import recognition_repository
from app.recorder import (
    MAX_CHANNELS, Recorder, RecorderAuthenticationError, RecorderChannelNotFoundError,
    RecorderError, RecorderSettings,
)
from app.snapshot_storage import DEFAULT_SNAPSHOT_DIR, SnapshotStorageError, save_snapshot
from app.snapshot_analysis import AnalysisQueue, QueueError

logger = logging.getLogger(__name__)
HONG_KONG = timezone(timedelta(hours=8))
RUN_TIME = time(9, 30)


def _is_run_day(day: date, public_holidays: Container[date]) -> bool:
    return day.weekday() < 5 and day not in public_holidays


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _execute(sql: str, *parameters) -> None:
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        cursor.execute(sql, *parameters)


def _eligible_recorders(*, include_missing_addresses: bool = False) -> list:
    address_filter = "" if include_missing_addresses else " AND internal_ip IS NOT NULL"
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        return cursor.execute(
            "SELECT shop_code, region, internal_ip, cam_qty, brand, admin_name, admin_password "
            f"FROM dbo.cctv_config WHERE brand IN ('DAHAU', 'HIKVISION'){address_filter} "
            "ORDER BY shop_code"
        ).fetchall()


def _add_item(run_date: date, recorder_index: int, shop: str, region: str, channel: int, camera: str,
              status: str, method: str, message: str):
    item_id = uuid4()
    _execute(
        "INSERT INTO dbo.inspection_run_items "
        "(id, run_date, recorder_index, shop_code, channel, camera_name, region, "
        "status, coverage_method, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        str(item_id), run_date, recorder_index, shop, channel, camera,
        region, status, method, message[:1000],
    )
    return item_id


def _recorder_identifier(shop: str, region: str, address: str) -> str:
    return recognition_repository.recorder_identifier(bytes.fromhex(recorder_key(shop, region, address)))


def _process_recorder(run_date: date, recorder_index: int, row,
                      seen_recorders: set[tuple[str, str, str]], snapshot_dir: Path,
                      shared_password: str, analyze: bool, plan: "CapturePlan | None" = None) -> None:
    plan = plan or _prepare_recorder(
        run_date, recorder_index, row, seen_recorders, shared_password, ChannelExclusions(snapshot_dir),
    )
    if plan is None:
        return
    recorder, recorder_id, shop, region, items = plan.recorder, plan.recorder_id, plan.shop, plan.region, plan.items
    _capture_channels(recorder, recorder_id, shop, region, items, run_date, snapshot_dir, analyze)


@dataclass
class CapturePlan:
    recorder: Recorder
    recorder_id: str
    shop: str
    region: str
    items: list[tuple[int, str, UUID]]


def _prepare_recorder(run_date: date, recorder_index: int, row,
                      seen_recorders: set[tuple[str, str, str]], shared_password: str,
                      exclusions: ChannelExclusions | None = None,
                      hikvision_password: str = "") -> CapturePlan | None:
    if len(row) >= 7:
        shop, region, ip, quantity, brand, username, db_password = row[:7]
    else:
        shop, region, ip, quantity = row[:4]
        brand, username, db_password = "DAHAU", "admin", ""
    shop = (shop or "").strip()
    region = (region or "").strip()
    try:
        if not shop or len(shop) > 50 or region not in ("Hong Kong", "Macau"):
            raise RecorderError("Shop code or region is invalid.")
        if not isinstance(quantity, int) or not 1 <= quantity <= MAX_CHANNELS:
            raise RecorderError(f"Configured camera count must be between 1 and {MAX_CHANNELS}.")
        brand = (brand or "DAHAU").strip().upper()
        username = (username or "admin").strip()
        password = (db_password or "").strip()
        if not password:
            password = hikvision_password if brand == "HIKVISION" else shared_password
        recorder = (Recorder(ip, username, password, brand)
                    if brand != "DAHAU" else Recorder(ip, username, password))
        configuration_key = (shop, region, recorder.base)
        if configuration_key in seen_recorders:
            raise RecorderError("Duplicate recorder configuration for this shop and address.")
        seen_recorders.add(configuration_key)
    except RecorderError as error:
        _add_item(run_date, recorder_index, shop[:50], region[:20],
                  0, "", "skipped", "none", str(error))
        return None

    recorder_id = _recorder_identifier(shop, region, recorder.base)
    excluded = exclusions.get(recorder_key(shop, region, recorder.base)) if exclusions is not None else {}
    items = []
    for channel in range(1, quantity + 1):
        name = f"Channel {channel}"
        identity = f"{name} [{recorder_id}]"
        item_id = _add_item(
            run_date, recorder_index, shop, region, channel, name,
            "skipped" if channel in excluded else "pending",
            "excluded" if channel in excluded else "configured_count",
            exclusion_message(excluded[channel]) if channel in excluded else "",
        )
        if channel not in excluded:
            items.append((channel, identity, item_id))
    return CapturePlan(recorder, recorder_id, shop, region, items)


def _capture_channels(recorder: Recorder, recorder_id: str, shop: str, region: str,
                      items: list[tuple[int, str, UUID]], run_date: date, snapshot_dir: Path,
                      analyze: bool) -> None:
    authentication_failed = False
    exclusions = ChannelExclusions(snapshot_dir)
    present: set[int] = set()
    not_found: set[int] = set()
    for channel, name, item_id in items:
        if authentication_failed:
            _execute(
                "UPDATE dbo.inspection_run_items SET status='failed', message=? WHERE id=?",
                "Not attempted: recorder authentication failed on a previous channel.", str(item_id),
            )
            continue
        try:
            image = recorder.snapshot(channel)
            captured_at = _utc_now()
            evidence = prepare_screenshot(image)
            present.add(channel)
            path = save_snapshot(snapshot_dir, run_date, recorder_id, channel, evidence)
            _execute(
                "UPDATE dbo.inspection_run_items SET status=?, captured_at=?, evidence=? WHERE id=?",
                "completed", _db_time(captured_at), pyodbc.Binary(evidence), str(item_id),
            )
            key = recorder_key(shop, region, recorder.base)
            try:
                AnalysisQueue(snapshot_dir).register_capture(item_id, path, key, channel, captured_at)
            except QueueError as error:
                logger.error("Scheduled snapshot saved but history indexing failed for %s: %s", item_id, error)
                _execute("UPDATE dbo.inspection_run_items SET message=? WHERE id=?",
                         f"Snapshot saved, but comparison history could not be indexed: {error}"[:1000], str(item_id))
            if analyze:
                try:
                    AnalysisQueue(snapshot_dir).enqueue(
                        f"scheduled:{run_date}", item_id, path, shop, region, channel, captured_at,
                        recorder_key=key, camera_name=name,
                    )
                except QueueError as error:
                    logger.error("Scheduled snapshot saved but AI enqueue failed for %s: %s", item_id, error)
                    _execute("UPDATE dbo.inspection_run_items SET message=? WHERE id=?",
                             f"Snapshot saved, but AI could not be queued: {error}"[:1000], str(item_id))
        except (RecorderError, InvalidScreenshot, SnapshotStorageError) as error:
            authentication_failed = isinstance(error, RecorderAuthenticationError)
            if isinstance(error, RecorderChannelNotFoundError):
                not_found.add(channel)
            _execute(
                "UPDATE dbo.inspection_run_items SET status='failed', message=? WHERE id=?",
                str(error)[:1000], str(item_id),
            )
    exclusions.record_results(recorder_key(shop, region, recorder.base), present, not_found)


def _mark_missed_and_interrupted(now: datetime, public_holidays: Container[date]) -> None:
    _execute(
        "UPDATE i SET status = CASE WHEN r.id IS NULL THEN 'interrupted' ELSE 'completed' END, "
        "message = CASE WHEN r.id IS NULL THEN 'Worker interrupted before completing this item.' ELSE i.message END "
        "FROM dbo.inspection_run_items i LEFT JOIN dbo.recognition_records r ON r.id=i.id "
        "JOIN dbo.inspection_runs runs ON runs.run_date=i.run_date "
        "WHERE runs.status='running' AND i.status IN ('pending', 'captured')"
    )
    _execute(
        "UPDATE dbo.inspection_runs SET status='interrupted', finished_at=? WHERE status='running'",
        _db_time(_utc_now()),
    )
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        last = cursor.execute("SELECT MAX(run_date) FROM dbo.inspection_runs").fetchone()[0]
    today = now.date()
    if last is None:
        last = today - timedelta(days=1)
    current = last + timedelta(days=1)
    while current < today or (current == today and now.time() >= RUN_TIME):
        if _is_run_day(current, public_holidays):
            _execute("INSERT INTO dbo.inspection_runs (run_date, status, finished_at) VALUES (?, 'missed', ?)",
                     current, _db_time(_utc_now()))
        current += timedelta(days=1)


def _run(today: date, snapshot_dir: Path, shared_password: str, analyze: bool,
         hikvision_password: str = "") -> None:
    _execute("INSERT INTO dbo.inspection_runs (run_date, status, started_at) VALUES (?, 'running', ?)",
             today, _db_time(_utc_now()))
    rows = _eligible_recorders()
    seen_recorders: set[tuple[str, str, str]] = set()
    exclusions = ChannelExclusions(snapshot_dir)
    plans = [(index, row, _prepare_recorder(today, index, row, seen_recorders, shared_password, exclusions, hikvision_password))
             for index, row in enumerate(rows, 1)]
    for index, row, plan in plans:
        if plan is None:
            continue
        try:
            _process_recorder(today, index, row, seen_recorders, snapshot_dir, shared_password, analyze, plan)
        except pyodbc.Error:
            raise
        except Exception as error:
            logger.error("Recorder processing failed unexpectedly (%s).", type(error).__name__)
            _execute(
                "UPDATE i SET status=CASE WHEN r.id IS NULL THEN 'failed' ELSE 'completed' END, "
                "message=CASE WHEN r.id IS NULL THEN 'Unexpected recorder processing error.' ELSE i.message END "
                "FROM dbo.inspection_run_items i LEFT JOIN dbo.recognition_records r ON i.id=r.id "
                "WHERE i.run_date=? AND i.recorder_index=? AND i.status IN ('pending', 'captured')",
                today, index,
            )
            _execute(
                "IF NOT EXISTS (SELECT 1 FROM dbo.inspection_run_items WHERE run_date=? "
                "AND recorder_index=? AND channel=0) "
                "INSERT INTO dbo.inspection_run_items "
                "(id, run_date, recorder_index, shop_code, channel, camera_name, region, status, coverage_method, message) "
                "VALUES (?, ?, ?, ?, 0, '', ?, 'failed', 'none', 'Unexpected recorder processing error.')",
                today, index, str(uuid4()), today, index, str(row[0] or "")[:50], str(row[1] or "")[:20],
            )
    _execute("UPDATE dbo.inspection_runs SET status='completed', finished_at=? WHERE run_date=?",
             _db_time(_utc_now()), today)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if os.environ.get("SCHEDULED_INSPECTION_ENABLED", "").lower() != "true":
        logger.info("Scheduled inspection is disabled.")
        return
    recorder_settings = RecorderSettings()
    shared_password = recorder_settings.dahau_password.get_secret_value()
    hikvision_password = recorder_settings.hikvision_password.get_secret_value()
    if ((not shared_password or not shared_password.strip())
            and (not hikvision_password or not hikvision_password.strip())):
        logger.error(
            "Set SCHEDULED_INSPECTION_DAHAU_PASSWORD or "
            "SCHEDULED_INSPECTION_HIKVISION_PASSWORD before starting the worker."
        )
        raise SystemExit(1)
    from app.review_schedules import ReviewScheduler
    from app.snapshot_analysis import AnalysisWorker, QueueSettings

    settings = QueueSettings()
    if not settings.worker_enabled:
        logger.info("Snapshot analysis and review scheduling are disabled.")
        return
    scheduler = ReviewScheduler(recorder_settings.snapshot_dir)
    worker = AnalysisWorker(recorder_settings.snapshot_dir, settings.concurrency)
    logger.info("Using frontend-managed review schedules; the legacy fixed daily trigger and mode are no longer used.")
    worker.start()
    scheduler.start()
    try:
        while True:
            clock.sleep(60)
    finally:
        scheduler.stop()
        worker.stop()


if __name__ == "__main__":
    try:
        main()
    except (pyodbc.Error, ValidationError, RuntimeError) as error:
        logger.error("Scheduled inspection worker stopped (%s).", type(error).__name__)
        raise SystemExit(1) from None
