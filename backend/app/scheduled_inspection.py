"""Opt-in daily worker for DAHAU recorders; device compatibility is unverified."""
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import logging
import os
import time as clock
from uuid import uuid4

import pyodbc
from pydantic import ValidationError

from app.database import DatabaseSettings, connect
from app.inspection import InvalidScreenshot, analyze_screenshot, prepare_screenshot
from app.recognition_models import RecognitionRecord
from app import recognition_repository
from app.recorder import Recorder, RecorderAuthenticationError, RecorderError
from app.vision_analysis import AnalysisError, VisionSettings

logger = logging.getLogger(__name__)
HONG_KONG = timezone(timedelta(hours=8))
RUN_TIME = time(9, 30)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _execute(sql: str, *parameters) -> None:
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        cursor.execute(sql, *parameters)


def _eligible_recorders() -> list:
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        return cursor.execute(
            "SELECT shop_code, region, internal_ip, admin_name, admin_password, cam_qty "
            "FROM dbo.cctv_config WHERE brand = 'DAHAU' AND shop_name IS NOT NULL "
            "AND internal_ip IS NOT NULL AND admin_name IS NOT NULL AND admin_password IS NOT NULL "
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
    fingerprint = hashlib.sha256(f"{region}\0{shop}\0{address}".encode("utf-8")).digest()
    with closing(connect(DatabaseSettings())) as connection, closing(connection.cursor()) as cursor:
        row = cursor.execute(
            "SELECT id FROM dbo.inspection_recorder_identities WHERE fingerprint=?", pyodbc.Binary(fingerprint),
        ).fetchone()
        if row is not None:
            return str(row[0])
        identifier = uuid4()
        cursor.execute(
            "INSERT INTO dbo.inspection_recorder_identities (fingerprint, id) VALUES (?, ?)",
            pyodbc.Binary(fingerprint), str(identifier),
        )
        return str(identifier)


def _process_recorder(run_date: date, recorder_index: int, row,
                      seen_recorders: set[tuple[str, str, str]]) -> None:
    shop, region, ip, username, password, quantity = row
    shop = (shop or "").strip()
    region = (region or "").strip()
    try:
        if not shop or len(shop) > 50 or region not in ("Hong Kong", "Macau"):
            raise RecorderError("Shop code or region is invalid.")
        recorder = Recorder(ip, username, password)
        recorder_key = (shop, region, recorder.base)
        if recorder_key in seen_recorders:
            raise RecorderError("Duplicate recorder configuration for this shop and address.")
        seen_recorders.add(recorder_key)
        discovery = recorder.discover(quantity)
    except RecorderError as error:
        _add_item(run_date, recorder_index, shop[:50], region[:20],
                  0, "", "skipped", "none", str(error))
        return

    recorder_id = _recorder_identifier(shop, region, recorder.base)
    items = []
    for channel in discovery.channels:
        identity = f"Channel {channel.number} [{recorder_id}]"
        item_id = _add_item(
            run_date, recorder_index, shop, region, channel.number, channel.name,
            "pending", discovery.method, discovery.warning,
        )
        items.append((channel, identity, item_id))
    authentication_failed = False
    for channel, name, item_id in items:
        if authentication_failed:
            _execute(
                "UPDATE dbo.inspection_run_items SET status='skipped', message=? WHERE id=?",
                "Recorder authentication failed on a previous channel.", str(item_id),
            )
            continue
        try:
            image = recorder.snapshot(channel.number)
            captured_at = _utc_now()
            evidence = prepare_screenshot(image)
            _execute(
                "UPDATE dbo.inspection_run_items SET status='captured', captured_at=?, evidence=? WHERE id=?",
                _db_time(captured_at), pyodbc.Binary(evidence), str(item_id),
            )
            baseline = recognition_repository.get_baseline_evidence(region, shop, name)
            analysis = analyze_screenshot(evidence, captured_at, baseline)
            record = RecognitionRecord(
                id=item_id, shop_code=shop, camera_name=name, region=region,
                filename=f"channel-{channel.number}.jpg", uploaded_at=_utc_now(), captured_at=captured_at,
                width=analysis.width, height=analysis.height,
                outcome="flagged" if any(check.status == "flagged" for check in analysis.checks) else "needs_review",
                review_status="new", review_note="", reviewed_at=None,
                analyzer_version=analysis.analyzer_version, checks=analysis.checks,
            )
            recognition_repository.save_record(record, analysis.evidence)
            _execute("UPDATE dbo.inspection_run_items SET status='completed' WHERE id=?", str(item_id))
        except (RecorderError, InvalidScreenshot, AnalysisError) as error:
            authentication_failed = isinstance(error, RecorderAuthenticationError)
            _execute(
                "UPDATE dbo.inspection_run_items SET status='failed', message=? WHERE id=?",
                (discovery.warning + " " + str(error))[:1000], str(item_id),
            )


def _mark_missed_and_interrupted(today: date) -> None:
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
    if last is None:
        last = today - timedelta(days=1)
    current = last + timedelta(days=1)
    now = datetime.now(HONG_KONG)
    while current < today or (current == today and now.time() >= RUN_TIME):
        _execute("INSERT INTO dbo.inspection_runs (run_date, status, finished_at) VALUES (?, 'missed', ?)",
                 current, _db_time(_utc_now()))
        current += timedelta(days=1)


def _run(today: date) -> None:
    _execute("INSERT INTO dbo.inspection_runs (run_date, status, started_at) VALUES (?, 'running', ?)",
             today, _db_time(_utc_now()))
    rows = _eligible_recorders()
    seen_recorders: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, 1):
        try:
            _process_recorder(today, index, row, seen_recorders)
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
    VisionSettings()
    with closing(connect(DatabaseSettings())) as lock_connection, closing(lock_connection.cursor()) as cursor:
        lock = cursor.execute(
            "SET NOCOUNT ON; DECLARE @result int; EXEC @result = sp_getapplock "
            "@Resource='cctv_scheduled_inspection', @LockMode='Exclusive', "
            "@LockOwner='Session', @LockTimeout=0; SELECT @result"
        ).fetchone()[0]
        if lock < 0:
            raise RuntimeError("Another scheduled inspection worker holds the database lock.")
        _mark_missed_and_interrupted(datetime.now(HONG_KONG).date())
        previous = datetime.now(HONG_KONG)
        while True:
            clock.sleep(10)
            now = datetime.now(HONG_KONG)
            scheduled = datetime.combine(now.date(), RUN_TIME, tzinfo=HONG_KONG)
            if previous < scheduled <= now:
                if now - scheduled <= timedelta(seconds=30):
                    _run(now.date())
                    now = datetime.now(HONG_KONG)
                _mark_missed_and_interrupted(now.date())
            elif now.date() != previous.date():
                _mark_missed_and_interrupted(now.date())
            previous = now


if __name__ == "__main__":
    try:
        main()
    except (pyodbc.Error, ValidationError, RuntimeError) as error:
        logger.error("Scheduled inspection worker stopped (%s).", type(error).__name__)
        raise SystemExit(1) from None
