"""Frontend-managed schedules. Times use Hong Kong time (UTC+08:00).

Definitions and occurrence claims share the local analysis SQLite store. Only
one scheduler owns its renewable lease; each occurrence has a stable batch ID.
Missed slots older than five minutes and overlapping captures are reported, not
replayed. Captures enqueue AI independently of model latency.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, time as day_time, timedelta, timezone
from functools import lru_cache
import logging
from pathlib import Path
import re
from threading import Event, Thread
import time
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from croniter import CroniterBadCronError, CroniterBadDateError, croniter
from holidays import country_holidays
import pyodbc
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import snapshot_batches as batches
from app.recorder import RecorderSettings
from app.scheduled_inspection import HONG_KONG
from app.snapshot_analysis import AnalysisQueue, LEASE_SECONDS, QueueError

logger = logging.getLogger(__name__)
OccurrenceStatus = Literal["running", "completed", "interrupted", "failed", "skipped", "missed", "stopped"]


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = Field(default=True, strict=True)
    cron: str = Field(default="30 9 * * 1", min_length=9, max_length=160)
    skip_holidays: bool = Field(default=True, strict=True)

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        value = " ".join(value.upper().split())
        if len(value.split()) != 5 or re.search(r"\b[RH]\b", value) or not croniter.is_valid(value):
            raise ValueError("Use a deterministic five-field cron expression: minute hour day month weekday.")
        return value


class ScheduleUpdate(ScheduleInput):
    revision: int = Field(ge=1)


class ReviewSchedule(ScheduleInput):
    id: UUID
    revision: int
    next_run: datetime | None


class Occurrence(BaseModel):
    id: UUID
    schedule_id: UUID
    schedule_name: str
    scheduled_at: datetime
    finished_at: datetime | None
    batch_id: UUID
    status: OccurrenceStatus
    message: str
    has_report: bool = False


class SchedulePage(BaseModel):
    schedules: list[ReviewSchedule]
    history: list[Occurrence]
    scheduler_online: bool
    timezone: Literal["Asia/Hong_Kong"] = "Asia/Hong_Kong"


@lru_cache(maxsize=32)
def _holidays(year: int):
    return country_holidays("HK", years=[year], observed=True)


def next_run(expression: str, after: datetime, skip_holidays: bool) -> datetime:
    if after.tzinfo is None:
        raise ValueError("An aware schedule anchor is required.")
    anchor = after.astimezone(HONG_KONG)
    horizon = anchor + timedelta(days=366 * 8)
    try:
        while anchor < horizon:
            candidate = croniter(expression, anchor, max_years_between_matches=8).get_next(datetime)
            if candidate > horizon:
                break
            if not skip_holidays or candidate.date() not in _holidays(candidate.year):
                return candidate.astimezone(timezone.utc)
            anchor = datetime.combine(candidate.date() + timedelta(days=1), day_time(), HONG_KONG) - timedelta(microseconds=1)
    except (CroniterBadCronError, CroniterBadDateError):
        pass
    raise ValueError("This schedule has no permitted execution within eight years.")


def _date(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc) if value is not None else None


class ScheduleConflict(RuntimeError):
    pass


class ScheduleStore:
    def __init__(self, root: Path):
        self.root = root
        self.queue = AnalysisQueue(root)
        with self.queue.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS review_scheduler (
                    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL DEFAULT '',
                    expires REAL NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO review_scheduler(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS review_schedules (
                    id TEXT PRIMARY KEY, definition TEXT NOT NULL, enabled INTEGER NOT NULL,
                    next_run REAL, revision INTEGER NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_occurrences (
                    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, schedule_name TEXT NOT NULL,
                    scheduled_at REAL NOT NULL, finished_at REAL, batch_id TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL,
                    UNIQUE(schedule_id, scheduled_at)
                );
                CREATE INDEX IF NOT EXISTS review_due ON review_schedules(enabled, next_run);
            """)

    def save(self, selection: ScheduleInput, now: datetime, schedule_id: UUID | None = None,
             revision: int | None = None) -> ReviewSchedule:
        # Validate impossible dates even for disabled drafts.
        following = next_run(selection.cron, now, selection.skip_holidays)
        with self.queue.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if schedule_id is None:
                if db.execute("SELECT COUNT(*) FROM review_schedules").fetchone()[0] >= 100:
                    raise ValueError("At most 100 review schedules can be stored.")
                schedule_id = uuid4()
                db.execute("INSERT INTO review_schedules VALUES(?,?,?,?,1,?)",
                           (str(schedule_id), selection.model_dump_json(), selection.enabled,
                            following.timestamp() if selection.enabled else None, now.timestamp()))
            else:
                row = db.execute("SELECT * FROM review_schedules WHERE id=?", (str(schedule_id),)).fetchone()
                if row is None or row["revision"] != revision:
                    raise ScheduleConflict("Schedule changed or was removed. Refresh before editing.")
                old = ScheduleInput.model_validate_json(row["definition"])
                due = following.timestamp() if selection.enabled else None
                if old.enabled and selection.enabled and old.cron == selection.cron and old.skip_holidays == selection.skip_holidays:
                    due = row["next_run"]
                db.execute("UPDATE review_schedules SET definition=?, enabled=?, next_run=?, revision=revision+1 WHERE id=?",
                           (selection.model_dump_json(), selection.enabled, due, str(schedule_id)))
            row = db.execute("SELECT * FROM review_schedules WHERE id=?", (str(schedule_id),)).fetchone()
        return self._schedule(row)

    @staticmethod
    def _schedule(row) -> ReviewSchedule:
        return ReviewSchedule(**ScheduleInput.model_validate_json(row["definition"]).model_dump(),
                              id=row["id"], revision=row["revision"], next_run=_date(row["next_run"]))

    def delete(self, schedule_id: UUID, revision: int) -> None:
        with self.queue.connection() as db:
            deleted = db.execute("DELETE FROM review_schedules WHERE id=? AND revision=?", (str(schedule_id), revision))
            if deleted.rowcount != 1:
                raise ScheduleConflict("Schedule changed or was removed. Refresh before editing.")

    def page(self) -> SchedulePage:
        with self.queue.connection() as db:
            schedules = db.execute("SELECT * FROM review_schedules ORDER BY created, id").fetchall()
            history = db.execute("SELECT * FROM review_occurrences ORDER BY scheduled_at DESC, id LIMIT 100").fetchall()
            expires = db.execute("SELECT expires FROM review_scheduler WHERE id=1").fetchone()[0]
        return SchedulePage(
            schedules=[self._schedule(row) for row in schedules],
            history=[self._occurrence(row).model_copy(update={
                "has_report": (self.root / "batches" / row["batch_id"] / "report.json").is_file(),
            }) for row in history], scheduler_online=expires > time.time(),
        )

    @staticmethod
    def _occurrence(row) -> Occurrence:
        return Occurrence(**{key: row[key] for key in ("id", "schedule_id", "schedule_name", "batch_id", "status", "message")},
                          scheduled_at=_date(row["scheduled_at"]), finished_at=_date(row["finished_at"]))

    def lead(self, owner: str, now: datetime) -> bool:
        with self.queue.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            control = db.execute("SELECT * FROM review_scheduler WHERE id=1").fetchone()
            if control["owner"] != owner:
                if control["expires"] > now.timestamp():
                    return False
                rows = db.execute("SELECT id, batch_id FROM review_occurrences WHERE status='running'").fetchall()
                for row in rows:
                    status = "interrupted"
                    message = "Scheduler restarted. Open the saved batch report for capture and AI details."
                    path = self.root / "batches" / row["batch_id"] / "report.json"
                    try:
                        if path.is_file():
                            report = batches.BatchReport.model_validate_json(path.read_text("utf-8"))
                            status = report.status if report.status != "running" else "interrupted"
                            if status in ("completed", "stopped"):
                                message = ""
                    except (OSError, ValidationError) as error:
                        logger.error("Scheduled batch recovery failed (%s).", type(error).__name__)
                        message = "Scheduler restarted and the batch report could not be read. Check backend logs."
                    db.execute("UPDATE review_occurrences SET status=?, message=?, finished_at=? WHERE id=?",
                               (status, message, now.timestamp(), row["id"]))
            db.execute("UPDATE review_scheduler SET owner=?, expires=? WHERE id=1",
                       (owner, now.timestamp() + LEASE_SECONDS))
        return True

    def release(self, owner: str) -> None:
        with self.queue.connection() as db:
            db.execute("UPDATE review_scheduler SET owner='', expires=0 WHERE id=1 AND owner=?", (owner,))

    def due(self, owner: str, now: datetime, busy: bool) -> list[Occurrence]:
        ready = []
        with self.queue.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            control = db.execute("SELECT * FROM review_scheduler WHERE id=1").fetchone()
            if control["owner"] != owner or control["expires"] <= now.timestamp():
                return []
            rows = db.execute("SELECT * FROM review_schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run, created, id",
                              (now.timestamp(),)).fetchall()
            for row in rows:
                definition = ScheduleInput.model_validate_json(row["definition"])
                following = next_run(definition.cron, now, definition.skip_holidays)
                planned = datetime.fromtimestamp(row["next_run"], timezone.utc)
                occurrence_id = uuid5(NAMESPACE_URL, f"retailvision-review:{row['id']}:{planned.isoformat()}")
                status: OccurrenceStatus = "running"
                message = ""
                if now - planned > timedelta(minutes=5):
                    status, message = "missed", "Backend was offline or unavailable. Missed checks are not replayed."
                elif busy:
                    status, message = "skipped", "Another scheduled capture is still running. This slot was not queued."
                db.execute(
                    "INSERT INTO review_occurrences VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(occurrence_id), row["id"], definition.name, planned.timestamp(),
                     None if status == "running" else now.timestamp(), str(occurrence_id), status, message, owner),
                )
                db.execute("UPDATE review_schedules SET next_run=? WHERE id=?", (following.timestamp(), row["id"]))
                if status == "running":
                    busy = True
                    saved = db.execute("SELECT * FROM review_occurrences WHERE id=?", (str(occurrence_id),)).fetchone()
                    ready.append(self._occurrence(saved))
        return ready

    def finish(self, occurrence: Occurrence, owner: str, status: OccurrenceStatus, message: str) -> None:
        with self.queue.connection() as db:
            db.execute("UPDATE review_occurrences SET status=?, message=?, finished_at=? "
                       "WHERE id=? AND owner=? AND status='running'",
                       (status, message[:1000], time.time(), str(occurrence.id), owner))


def capture_review(root: Path, occurrence: Occurrence, stopped: Event) -> tuple[OccurrenceStatus, str]:
    try:
        settings = RecorderSettings()
        password = settings.dahau_password.get_secret_value()
        hikvision_password = settings.hikvision_password.get_secret_value()
        if not password.strip() and not hikvision_password.strip():
            return "failed", "Shared snapshot capture credentials are not configured."
        with ExitStack() as cleanup:
            connection = batches._acquire_lock()
            if connection is None:
                return "skipped", "Another capture holds the batch lock. This slot was not queued."
            cleanup.callback(batches._release_lock, connection)
            batches._recover_interrupted(root)
            existing = root / "batches" / str(occurrence.batch_id) / "report.json"
            if existing.is_file():
                report = batches.BatchReport.model_validate_json(existing.read_text("utf-8"))
            else:
                report, recorders = batches._plan_batch(
                    password, root, hikvision_password= hikvision_password,
                )
                if not report.items:
                    return "failed", "No supported recorders are configured for capture."
                report.id = occurrence.batch_id
                report.auto_analyze = True
                report.schedule_name = occurrence.schedule_name
                batches._write_report(root, report)
                # The capture loop takes ownership of releasing the SQL session.
                cleanup.pop_all()
                batches._capture_batch(root, report, recorders, connection, stopped=stopped)
            if report.status == "completed":
                return "completed", report.analysis_error
            if report.status == "stopped":
                return "stopped", report.analysis_error
            return "interrupted", report.error or "Capture was interrupted. Open the batch report."
    except (QueueError, OSError, ValueError, pyodbc.Error) as error:
        logger.error("Scheduled review %s failed (%s).", occurrence.id, type(error).__name__)
        return "failed", "Scheduled capture failed. Check backend configuration, database and archive."


class ReviewScheduler:
    def __init__(self, root: Path):
        self.store = ScheduleStore(root)
        self.owner = str(uuid4())
        self.stopped = Event()
        self.thread = Thread(target=self.run, name="review-scheduler", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join()

    def run(self) -> None:
        active: tuple[Occurrence, Future] | None = None
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="scheduled-capture") as pool:
                while not self.stopped.is_set() or active:
                    try:
                        now = datetime.now(timezone.utc)
                        if active and active[1].done():
                            status, message = active[1].result()
                            self.store.finish(active[0], self.owner, status, message)
                            active = None
                        if self.store.lead(self.owner, now):
                            if not self.stopped.is_set():
                                for occurrence in self.store.due(self.owner, now, active is not None):
                                    active = (occurrence, pool.submit(capture_review, self.store.root, occurrence, self.stopped))
                    except QueueError as error:
                        logger.error("Review scheduler paused: %s", error)
                        if self.stopped.is_set() and (active is None or active[1].done()):
                            break
                    except KeyboardInterrupt:
                        self.stopped.set()
                    if self.stopped.is_set():
                        time.sleep(0.1)
                    else:
                        self.stopped.wait(2)
        finally:
            try:
                self.store.release(self.owner)
            except QueueError as error:
                logger.error("Review scheduler lease could not be released: %s", error)
