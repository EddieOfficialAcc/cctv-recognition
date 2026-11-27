"""Durable, capture-independent AI queue in <snapshot_dir>/analysis.sqlite3.

The web backend drains this queue. Scheduled capture only enqueues work, so it
can finish while the backend is offline. A renewable SQLite leader lease bounds
concurrency across backend processes sharing the archive. Expired work resumes.
Completed items are never resubmitted; cached model results survive SQL retries.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, time as datetime_time, timedelta, timezone
import hashlib
import logging
from pathlib import Path, PurePosixPath
import sqlite3
from threading import Event, Thread
import time
from typing import Literal
from uuid import UUID, uuid4

import pyodbc
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import recognition_repository as repository
from app.inspection import MAX_UPLOAD_BYTES, InvalidScreenshot, analyze_screenshot, prepare_screenshot
from app.recognition_models import RecognitionRecord, Region
from app.vision_analysis import AnalysisError, RetryableAnalysisError

logger = logging.getLogger(__name__)
LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
HONG_KONG = timezone(timedelta(hours=8))
AnalysisStatus = Literal["queued", "analyzing", "completed", "failed"]


class QueueError(RuntimeError):
    pass


class QueueSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SNAPSHOT_ANALYSIS_", extra="ignore", hide_input_in_errors=True,
        env_file=Path(__file__).resolve().parents[1] / ".env",
    )
    worker_enabled: bool = True
    concurrency: int = Field(default=2, ge=1, le=4)


class AnalysisInput(BaseModel):
    id: UUID
    shop_code: str
    region: Region
    channel: int = Field(ge=1)
    captured_at: datetime | None
    recorder_key: str | None = None
    camera_name: str | None = None
    path: str
    digest: str


class AnalysisItem(BaseModel):
    id: UUID
    shop_code: str
    region: Region
    channel: int
    status: AnalysisStatus
    attempts: int
    message: str
    retry_at: datetime | None
    record: RecognitionRecord | None


class AnalysisSummary(BaseModel):
    total: int
    queued: int
    analyzing: int
    completed: int
    failed: int
    flagged: int
    worker_online: bool
    auto_analyze: bool


class AnalysisPage(BaseModel):
    items: list[AnalysisItem]
    summary: AnalysisSummary
    total: int
    page: int
    page_size: int


def _read_image(root: Path, relative: str) -> bytes:
    path = root.joinpath(*PurePosixPath(relative.replace("\\", "/")).parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise QueueError("Snapshot archive path is invalid.")
    with path.open("rb") as source:
        content = source.read(MAX_UPLOAD_BYTES + 1)
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise QueueError("Snapshot is empty or exceeds 10 MB.")
    return content


class AnalysisQueue:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "analysis.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS control (
                    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL DEFAULT '',
                    expires REAL NOT NULL DEFAULT 0, paused_until REAL NOT NULL DEFAULT 0,
                    auto_analyze INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO control(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, input TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    available REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    result TEXT, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_source ON jobs(source, created, id);
                CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status, available, created);
                CREATE TABLE IF NOT EXISTS captures (
                    id TEXT PRIMARY KEY, recorder_key TEXT NOT NULL, channel INTEGER NOT NULL,
                    captured REAL NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS captures_lookup ON captures(recorder_key, channel, captured DESC);
                CREATE TABLE IF NOT EXISTS comparisons (
                    job_id TEXT PRIMARY KEY, captured_at TEXT, evidence BLOB
                );
            """)

    @contextmanager
    def connection(self):
        db = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            with db:
                yield db
        except (sqlite3.Error, OSError) as error:
            logger.error("AI queue storage failed (%s).", type(error).__name__)
            raise QueueError("AI review storage unavailable. Check archive permissions and disk space.") from None
        finally:
            if db is not None:
                db.close()

    def auto_analyze(self) -> bool:
        with self.connection() as db:
            return bool(db.execute("SELECT auto_analyze FROM control WHERE id=1").fetchone()[0])

    def set_auto_analyze(self, enabled: bool) -> None:
        with self.connection() as db:
            db.execute("UPDATE control SET auto_analyze=? WHERE id=1", (enabled,))

    def capture_ids(self) -> set[str]:
        with self.connection() as db:
            return {row[0] for row in db.execute("SELECT id FROM captures")}

    def register_capture(self, item_id: UUID, path: Path, recorder_key: str,
                         channel: int, captured_at: datetime, *, historical: bool = False) -> None:
        if captured_at.utcoffset() is None:
            raise QueueError("Capture time must include a timezone offset.")
        try:
            relative = path.resolve().relative_to(self.root.resolve()).as_posix()
            try:
                content = _read_image(self.root, relative)
            except (OSError, QueueError):
                if not historical:
                    raise
                logger.warning("Historical snapshot %s is unavailable in the archive.", item_id)
                content = None
        except (OSError, ValueError):
            raise QueueError("Saved snapshot is missing or outside the archive.") from None
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO captures(id, recorder_key, channel, captured, path, digest) "
                "VALUES(?,?,?,?,?,?)",
                (str(item_id), recorder_key, channel, captured_at.timestamp(), relative,
                 hashlib.sha256(content).hexdigest() if content is not None else ""),
            )

    def comparison(self, item_id: UUID) -> tuple[datetime, bytes] | None:
        with self.connection() as db:
            row = db.execute("SELECT captured_at, evidence FROM comparisons WHERE job_id=?",
                             (str(item_id),)).fetchone()
        if row is None or row["evidence"] is None:
            return None
        return datetime.fromisoformat(row["captured_at"]), bytes(row["evidence"])

    def select_comparison(self, item: AnalysisInput) -> tuple[datetime, bytes] | None:
        with self.connection() as db:
            existing = db.execute("SELECT captured_at, evidence FROM comparisons WHERE job_id=?",
                                  (str(item.id),)).fetchone()
            if existing is not None:
                return ((datetime.fromisoformat(existing["captured_at"]), bytes(existing["evidence"]))
                        if existing["evidence"] is not None else None)
            candidate = None
            if item.recorder_key and item.captured_at and item.captured_at.utcoffset() is not None:
                day = item.captured_at.astimezone(HONG_KONG).date()
                end = datetime.combine(day, datetime_time.min, tzinfo=HONG_KONG)
                start = end - timedelta(days=1)
                candidate = db.execute(
                    "SELECT captured, path, digest FROM captures WHERE recorder_key=? AND channel=? "
                    "AND captured>=? AND captured<? ORDER BY captured DESC, id DESC LIMIT 1",
                    (item.recorder_key, item.channel, start.timestamp(), end.timestamp()),
                ).fetchone()
            captured_at, evidence = None, None
            if candidate is not None:
                try:
                    content = _read_image(self.root, candidate["path"])
                    if hashlib.sha256(content).hexdigest() != candidate["digest"]:
                        raise QueueError("Previous-day snapshot changed after capture.")
                    evidence = prepare_screenshot(content)
                    captured_at = datetime.fromtimestamp(candidate["captured"], timezone.utc)
                except (OSError, QueueError, InvalidScreenshot) as error:
                    logger.warning("Previous-day snapshot unavailable for %s (%s).", item.id, type(error).__name__)
            db.execute("INSERT OR IGNORE INTO comparisons(job_id, captured_at, evidence) VALUES(?,?,?)",
                       (str(item.id), captured_at.isoformat() if captured_at else None, evidence))
        return self.comparison(item.id)

    def enqueue(self, source: str, item_id: UUID, path: Path, shop: str, region: str,
                channel: int, captured_at: datetime | None, *, recorder_key: str | None = None,
                camera_name: str | None = None) -> None:
        try:
            relative = path.resolve().relative_to(self.root.resolve()).as_posix()
            content = _read_image(self.root, relative)
        except (OSError, ValueError):
            raise QueueError("Saved snapshot is missing or outside the archive.") from None
        entry = AnalysisInput(
            id=item_id, shop_code=shop, region=region, channel=channel, captured_at=captured_at,
            recorder_key=recorder_key, camera_name=camera_name, path=relative,
            digest=hashlib.sha256(content).hexdigest(),
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT source, input FROM jobs WHERE id=?", (str(item_id),)).fetchone()
            if old is not None:
                previous = AnalysisInput.model_validate_json(old["input"])
                if old["source"] != source or previous.digest != entry.digest:
                    raise QueueError("An already queued snapshot changed. Capture a new batch instead.")
                return
            db.execute("INSERT INTO jobs(id, source, input, created) VALUES(?,?,?,?)",
                       (str(item_id), source, entry.model_dump_json(), time.time()))

    def page(self, source: str, page: int = 1, page_size: int = 20,
             status: AnalysisStatus | None = None) -> AnalysisPage:
        with self.connection() as db:
            counts = {row[0]: row[1] for row in db.execute(
                "SELECT status, COUNT(*) FROM jobs WHERE source=? GROUP BY status", (source,),
            )}
            flagged = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE source=? AND status='completed' "
                "AND json_extract(result, '$.outcome')='flagged'", (source,),
            ).fetchone()[0]
            control = db.execute("SELECT expires, auto_analyze FROM control WHERE id=1").fetchone()
            clause = "source=?" + (" AND status=?" if status else "")
            parameters = (source, status) if status else (source,)
            rows = db.execute(
                f"SELECT * FROM jobs WHERE {clause} ORDER BY created, id LIMIT ? OFFSET ?",
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()
        return AnalysisPage(
            items=[AnalysisItem(
                **{key: row[key] for key in ("id", "status", "attempts", "message")},
                **AnalysisInput.model_validate_json(row["input"]).model_dump(
                    include={"shop_code", "region", "channel"},
                ),
                retry_at=datetime.fromtimestamp(row["available"], timezone.utc) if row["available"] else None,
                record=RecognitionRecord.model_validate_json(row["result"])
                if row["status"] == "completed" else None,
            ) for row in rows],
            summary=AnalysisSummary(
                total=sum(counts.values()), flagged=flagged,
                worker_online=control["expires"] > time.time(), auto_analyze=bool(control["auto_analyze"]),
                **{state: counts.get(state, 0) for state in ("queued", "analyzing", "completed", "failed")},
            ),
            total=counts.get(status, 0) if status else sum(counts.values()), page=page, page_size=page_size,
        )

    def retry_failed(self, source: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='queued', attempts=0, available=0, message='' "
                       "WHERE source=? AND status='failed'", (source,))

    def lead(self, owner: str, now: float) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            control = db.execute("SELECT owner, expires FROM control WHERE id=1").fetchone()
            if control["owner"] != owner:
                if control["expires"] > now:
                    return False
                db.execute(
                    "UPDATE jobs SET status=CASE WHEN attempts<? OR result IS NOT NULL THEN 'queued' ELSE 'failed' END, "
                    "message='AI worker was interrupted; unfinished work requires recovery.' WHERE status='analyzing'",
                    (MAX_ATTEMPTS,),
                )
            db.execute("UPDATE control SET owner=?, expires=? WHERE id=1", (owner, now + LEASE_SECONDS))
            return True

    def release(self, owner: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE control SET owner='', expires=0 WHERE id=1 AND owner=?", (owner,))

    def claim(self, owner: str):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            control = db.execute("SELECT * FROM control WHERE id=1").fetchone()
            now = time.time()
            if control["owner"] != owner or control["expires"] <= now or control["paused_until"] > now:
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' AND available<=? ORDER BY created, id LIMIT 1", (now,),
            ).fetchone()
            if row:
                db.execute("UPDATE jobs SET status='analyzing', attempts=attempts+1, message='', available=0 WHERE id=?",
                           (row["id"],))
            return row

    def cache_result(self, item_id: str, record: RecognitionRecord) -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET result=? WHERE id=?", (record.model_dump_json(), item_id))

    def finish(self, item_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='completed', message='', available=0 WHERE id=?", (item_id,))

    def fail(self, row, message: str, *, delay: float | None = None, rate_limited: bool = False) -> None:
        retry = delay is not None and row["attempts"] + 1 < MAX_ATTEMPTS
        available = time.time() + delay if delay is not None else 0
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=?, message=?, available=? WHERE id=?",
                       ("queued" if retry else "failed", message, available if retry else 0, row["id"]))
            if rate_limited:
                db.execute("UPDATE control SET paused_until=MAX(paused_until, ?) WHERE id=1", (available,))


def process_item(queue: AnalysisQueue, row) -> None:
    try:
        item = AnalysisInput.model_validate_json(row["input"])
        existing = repository.get_record(item.id)
        if existing is not None:
            queue.cache_result(row["id"], existing)
            queue.finish(row["id"])
            return
        image = _read_image(queue.root, item.path)
        if hashlib.sha256(image).hexdigest() != item.digest:
            raise QueueError("Saved snapshot changed after it was queued. Capture a new batch.")
        if row["result"]:
            record = RecognitionRecord.model_validate_json(row["result"])
        else:
            name = item.camera_name
            if not name:
                identifier = repository.recorder_identifier(bytes.fromhex(item.recorder_key)) if item.recorder_key else str(item.id)
                name = f"Channel {item.channel} [{identifier}]"
            comparison = queue.select_comparison(item)
            analysis = analyze_screenshot(
                image, item.captured_at, previous_day=comparison[1] if comparison else None, daily=True,
            )
            record = RecognitionRecord(
                id=item.id, shop_code=item.shop_code, camera_name=name, region=item.region,
                filename=f"channel-{item.channel:03d}.jpeg",
                uploaded_at=datetime.now(timezone.utc), captured_at=item.captured_at,
                width=analysis.width, height=analysis.height, checks=analysis.checks,
                analyzer_version=analysis.analyzer_version,
                outcome="flagged" if any(check.status == "flagged" for check in analysis.checks) else "needs_review",
                review_status="new", review_note="", reviewed_at=None,
            )
            queue.cache_result(row["id"], record)
        repository.save_record(record, image)
        queue.finish(row["id"])
    except RetryableAnalysisError as error:
        queue.fail(row, str(error), delay=max(error.retry_after or 0, 4 * 2 ** row["attempts"]),
                   rate_limited=error.rate_limited)
    except pyodbc.Error:
        logger.error("AI result database operation failed for %s.", row["id"])
        queue.fail(row, "Recognition storage unavailable. Retry will reuse any cached AI result.",
                   delay=4 * 2 ** row["attempts"])
    except (AnalysisError, QueueError, OSError, ValueError) as error:
        logger.error("AI review %s failed (%s).", row["id"], type(error).__name__)
        message = str(error) if isinstance(error, (AnalysisError, InvalidScreenshot, QueueError)) else (
            "Snapshot or AI configuration unavailable. Check backend configuration and the archive."
        )
        queue.fail(row, message)


class AnalysisWorker:
    def __init__(self, root: Path, concurrency: int):
        self.queue = AnalysisQueue(root)
        self.concurrency = concurrency
        self.owner = str(uuid4())
        self.stopped = Event()
        self.thread = Thread(target=self.run, name="snapshot-analysis", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join()

    def run(self) -> None:
        active: set[Future] = set()
        recover = False
        history_indexed = False
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="vision") as pool:
                while not self.stopped.is_set() or active:
                    try:
                        if not history_indexed:
                            from app.snapshot_batches import index_capture_history
                            index_capture_history(self.queue)
                            history_indexed = True
                        for future in tuple(active):
                            if future.done():
                                active.remove(future)
                                try:
                                    future.result()
                                except QueueError as error:
                                    logger.error("AI task could not persist its state (%s).", type(error).__name__)
                                    recover = True
                        if recover and not active:
                            self.queue.release(self.owner)
                            self.owner = str(uuid4())
                            recover = False
                        if self.queue.lead(self.owner, time.time()):
                            while not self.stopped.is_set() and not recover and len(active) < self.concurrency:
                                row = self.queue.claim(self.owner)
                                if row is None:
                                    break
                                active.add(pool.submit(process_item, self.queue, row))
                    except QueueError as error:
                        logger.error("AI worker paused: %s", error)
                    if self.stopped.is_set():
                        time.sleep(0.1)
                    else:
                        self.stopped.wait(1)
        finally:
            try:
                self.queue.release(self.owner)
            except QueueError as error:
                logger.error("AI worker lease could not be released: %s", error)
