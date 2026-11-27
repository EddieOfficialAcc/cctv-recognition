from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
import pyodbc

from app import review_schedules as schedules, snapshot_batches as batches
from app.auth import require_user
from app.main import app
from app.recorder import RecorderSettings
from app.snapshot_analysis import AnalysisQueue, QueueError


@patch.dict(os.environ, {"SNAPSHOT_ANALYSIS_WORKER_ENABLED": "false"})
class ReviewSchedulesTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = schedules.ScheduleStore(self.root)
        self.before = datetime(2026, 11, 23, 9, 29, tzinfo=schedules.HONG_KONG)
        self.due = self.before + timedelta(minutes=1)
        self.selection = schedules.ScheduleInput(name="Weekly review")
        self.settings = RecorderSettings(dahau_password="test-only", snapshot_dir=self.root, _env_file=None)

    def claim(self):
        self.store.save(self.selection, self.before)
        self.assertTrue(self.store.lead("owner", self.due))
        return self.store.due("owner", self.due, False)[0]

    def test_weekly_default_and_multiple_days_times_and_months_use_hong_kong_time(self):
        self.assertEqual(schedules.next_run(self.selection.cron, self.before, True),
                         datetime(2026, 11, 23, 1, 30, tzinfo=timezone.utc))
        cursor = datetime(2026, 1, 5, 8, 59, tzinfo=schedules.HONG_KONG)
        actual = []
        for _ in range(5):
            cursor = schedules.next_run("0,30 9,17 * 1,7 1,3", cursor, False)
            actual.append(cursor.astimezone(schedules.HONG_KONG).strftime("%m-%d %H:%M"))
        self.assertEqual(actual, ["01-05 09:00", "01-05 09:30", "01-05 17:00", "01-05 17:30", "01-07 09:00"])

    def test_month_ends_leap_day_and_impossible_dates(self):
        anchor = datetime(2026, 2, 1, tzinfo=schedules.HONG_KONG)
        self.assertEqual(schedules.next_run("30 9 L * *", anchor, False).astimezone(schedules.HONG_KONG).day, 28)
        self.assertEqual(schedules.next_run("0 9 29 2 *", anchor, False).astimezone(schedules.HONG_KONG).date(), date(2028, 2, 29))
        with self.assertRaises(ValueError):
            schedules.next_run("0 9 31 2 *", anchor, False)
        for expression in ("@daily", "* * * * * *", "H * * * *", "R * * * *", "60 9 * * *"):
            with self.subTest(expression=expression), self.assertRaises(ValidationError):
                schedules.ScheduleInput(name="Invalid", cron=expression)

    def test_holidays_skip_whole_dates_without_minute_by_minute_search(self):
        anchor = datetime(2026, 12, 24, 23, 59, tzinfo=schedules.HONG_KONG)
        with patch("app.review_schedules._holidays", return_value={date(2026, 12, 25)}) as calendar:
            following = schedules.next_run("* * * * *", anchor, True).astimezone(schedules.HONG_KONG)
        self.assertEqual(following, datetime(2026, 12, 26, tzinfo=schedules.HONG_KONG))
        self.assertLessEqual(calendar.call_count, 3)
        self.assertEqual(schedules.next_run("* * * * *", anchor, False).astimezone(schedules.HONG_KONG).day, 25)

    def test_persistence_pause_resume_revision_conflicts_and_name_edits_preserve_due_time(self):
        first = self.store.save(self.selection, self.before)
        renamed = self.selection.model_copy(update={"name": "Renamed"})
        updated = self.store.save(renamed, self.due + timedelta(seconds=1), first.id, first.revision)
        self.assertEqual((updated.next_run, updated.revision), (first.next_run, 2))
        with self.assertRaises(schedules.ScheduleConflict):
            self.store.save(renamed, self.before, first.id, 1)
        paused = self.store.save(renamed.model_copy(update={"enabled": False}), self.due, first.id, 2)
        self.assertIsNone(paused.next_run)
        self.assertTrue(self.store.lead("owner", self.due))
        self.assertEqual(self.store.due("owner", self.due, False), [])
        resumed = self.store.save(renamed, self.due, first.id, paused.revision)
        self.assertEqual(resumed.next_run, first.next_run + timedelta(days=7))
        reopened = schedules.ScheduleStore(self.root)
        self.assertEqual(reopened.page().schedules, [resumed])
        with self.assertRaises(schedules.ScheduleConflict):
            reopened.delete(first.id, 1)
        reopened.delete(first.id, resumed.revision)
        self.assertEqual(self.store.page().schedules, [])

    def test_shared_lease_claims_once_and_reports_overlap(self):
        self.store.save(self.selection, self.before)
        self.store.save(self.selection.model_copy(update={"name": "Other"}), self.before)
        other = schedules.ScheduleStore(self.root)
        self.assertTrue(self.store.lead("owner", self.due))
        self.assertFalse(other.lead("other", self.due))
        self.assertEqual(other.due("other", self.due, False), [])
        claimed = self.store.due("owner", self.due, False)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(self.store.due("owner", self.due, False), [])
        self.assertCountEqual([row.status for row in other.page().history], ["running", "skipped"])
        self.store.delete(self.store.page().schedules[0].id, 1)
        self.assertEqual(len(self.store.page().history), 2)

    def test_downtime_records_one_missed_slot_and_advances_without_replaying_backlog(self):
        self.store.save(self.selection, self.before)
        later = self.due + timedelta(days=14, minutes=6)
        self.store.lead("owner", later)
        self.assertEqual(self.store.due("owner", later, False), [])
        page = self.store.page()
        self.assertEqual([row.status for row in page.history], ["missed"])
        self.assertGreater(page.schedules[0].next_run, later)

    def test_restart_recovers_completed_batch_and_never_reclaims_same_slot(self):
        occurrence = self.claim()
        report = batches.BatchReport(id=occurrence.batch_id, status="completed", started_at=self.due,
                                     finished_at=self.due, items=[], schedule_name="Weekly review")
        batches._write_report(self.root, report)
        later = self.due + timedelta(seconds=61)
        self.assertTrue(self.store.lead("replacement", later))
        self.assertEqual(self.store.due("replacement", later, False), [])
        recovered = self.store.page().history[0]
        self.assertEqual((recovered.id, recovered.batch_id, recovered.status, recovered.has_report),
                         (occurrence.id, occurrence.batch_id, "completed", True))

    def test_restart_without_saved_report_is_explicitly_interrupted(self):
        self.claim()
        self.store.lead("replacement", self.due + timedelta(seconds=61))
        row = self.store.page().history[0]
        self.assertEqual(row.status, "interrupted")
        self.assertFalse(row.has_report)
        self.assertIn("restarted", row.message)

    def capture_setup(self):
        image = BytesIO()
        Image.new("RGB", (32, 24), "gray").save(image, "JPEG")
        for name, result in (
            ("app.review_schedules.RecorderSettings", self.settings),
            ("app.snapshot_batches._eligible_recorders", [("001", "Hong Kong", "192.0.2.1", 2)]),
            ("app.snapshot_batches._acquire_lock", MagicMock()),
            ("app.snapshot_batches.Recorder.snapshot", image.getvalue()),
        ):
            mock = patch(name, return_value=result)
            self.addCleanup(mock.stop)
            setattr(self, name.split(".")[-1], mock.start())
        release = patch("app.snapshot_batches._release_lock")
        self.addCleanup(release.stop)
        self.release = release.start()

    def test_fresh_capture_uses_existing_exclusions_jpeg_reports_and_ai_queue_without_model_calls(self):
        occurrence = self.claim()
        self.capture_setup()
        with patch("app.snapshot_analysis.analyze_screenshot") as model:
            status, message = schedules.capture_review(self.root, occurrence, Event())
            self.assertEqual((status, message), ("completed", ""))
            report = batches._read_report(self.root, occurrence.batch_id)
            self.assertEqual((report.summary().saved, report.schedule_name, report.auto_analyze), (2, "Weekly review", True))
            self.assertEqual(len(list(self.root.rglob("*.jpeg"))), 2)
            self.assertEqual(AnalysisQueue(self.root).page(f"batch:{occurrence.batch_id}").summary.queued, 2)
            self.assertEqual(schedules.capture_review(self.root, occurrence, Event()), ("completed", ""))
            self.assertEqual(self.snapshot.call_count, 2)
            self.assertEqual(self.release.call_count, 2)
            model.assert_not_called()

    def test_scheduled_capture_includes_hikvision_and_dahua(self):
        occurrence = self.claim()
        self.capture_setup()
        self._eligible_recorders.return_value = [
            ("001", "Hong Kong", "192.0.2.1", 1, "DAHAU", "admin", "dahua-test"),
            ("002", "Hong Kong", "192.0.2.2", 1, "HIKVISION", "admin", "hik-test"),
        ]
        captured = []
        image = self.snapshot.return_value
        def capture(recorder, channel):
            captured.append((recorder.brand, channel))
            return image
        with patch("app.snapshot_batches.Recorder.snapshot", new=capture):
            status, _ = schedules.capture_review(self.root, occurrence, Event())
        self.assertEqual(status, "completed")
        self.assertEqual(captured, [("DAHAU", 1), ("HIKVISION", 1)])

    def test_shutdown_interrupts_between_cameras_without_losing_report_or_lock(self):
        occurrence = self.claim()
        self.capture_setup()
        stopped = Event()
        stopped.set()
        status, message = schedules.capture_review(self.root, occurrence, stopped)
        self.assertEqual(status, "interrupted")
        self.assertIn("Backend stopped", message)
        report = batches._read_report(self.root, occurrence.batch_id)
        self.assertEqual(report.summary().interrupted, 2)
        self.release.assert_called_once()
        self.snapshot.assert_not_called()

    def test_busy_or_failed_capture_releases_lock_and_does_not_run_model(self):
        occurrence = self.claim()
        self.capture_setup()
        self._acquire_lock.return_value = None
        self.assertEqual(schedules.capture_review(self.root, occurrence, Event())[0], "skipped")
        self.release.assert_not_called()
        self._acquire_lock.return_value = MagicMock()
        with patch("app.snapshot_batches._plan_batch", side_effect=pyodbc.Error("test-only")):
            self.assertEqual(schedules.capture_review(self.root, occurrence, Event())[0], "failed")
        self.release.assert_called_once()
        self.snapshot.assert_not_called()

    def test_sql_lock_release_failure_is_reported_instead_of_crashing_coordinator(self):
        occurrence = self.claim()
        self.capture_setup()
        batches._write_report(self.root, batches.BatchReport(
            id=occurrence.batch_id, status="completed", started_at=self.due, items=[],
        ))
        self.release.side_effect = pyodbc.Error("synthetic release failure")
        self.assertEqual(schedules.capture_review(self.root, occurrence, Event())[0], "failed")
        self.snapshot.assert_not_called()

    def test_shutdown_with_unavailable_history_storage_leaves_occurrence_recoverable(self):
        self.store.save(schedules.ScheduleInput(name="Minute", cron="* * * * *", skip_holidays=False),
                        datetime.now(timezone.utc) - timedelta(minutes=2))
        started = Event()

        def capture(root, occurrence, stopped):
            started.set()
            stopped.wait(5)
            return "completed", ""

        with patch("app.review_schedules.capture_review", side_effect=capture):
            worker = schedules.ReviewScheduler(self.root)
            with patch.object(worker.store, "finish", side_effect=QueueError("Synthetic storage failure")) as finish:
                worker.start()
                self.assertTrue(started.wait(3))
                stopper = Thread(target=worker.stop, daemon=True)
                stopper.start()
                stopper.join(3)
                hung = stopper.is_alive()
                if hung:
                    finish.side_effect = None
                    stopper.join(4)
                self.assertFalse(hung, "Unavailable history must not prevent backend shutdown.")
        self.assertEqual(self.store.page().history[0].status, "running")
        self.store.lead("replacement", datetime.now(timezone.utc))
        self.assertEqual(self.store.page().history[0].status, "interrupted")

    def test_coordinator_finishes_and_stops_even_after_losing_leadership(self):
        now = datetime.now(timezone.utc)
        self.store.save(schedules.ScheduleInput(name="Minute", cron="* * * * *", skip_holidays=False), now - timedelta(minutes=2))
        started = Event()

        def capture(root, occurrence, stopped):
            started.set()
            stopped.wait(5)
            return "completed", ""

        with patch("app.review_schedules.capture_review", side_effect=capture) as mocked:
            worker = schedules.ReviewScheduler(self.root)
            worker.start()
            self.assertTrue(started.wait(3))
            with self.store.queue.connection() as db:
                db.execute("UPDATE review_scheduler SET owner='other', expires=?", (time.time() + 3600,))
            stopper = Thread(target=worker.stop, daemon=True)
            stopper.start()
            stopper.join(3)
            hung = stopper.is_alive()
            if hung:
                self.store.release("other")
                stopper.join(4)
            self.assertFalse(hung, "Shutdown must not wait for another scheduler's lease.")
        mocked.assert_called_once()
        self.assertEqual(self.store.page().history[0].status, "completed")

    def test_schedule_api_persistence_preview_configuration_and_conflict(self):
        app.dependency_overrides[require_user] = lambda: "test-user"
        self.addCleanup(app.dependency_overrides.pop, require_user)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        with patch("app.review_schedule_api.RecorderSettings", return_value=self.settings), patch(
            "app.snapshot_analysis_api.vision_configured", return_value=True,
        ), TestClient(app, headers=headers) as client, patch("app.review_schedules.capture_review") as capture:
            body = self.selection.model_dump()
            preview = client.post("/api/review-schedules/preview", json=body)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(len(preview.json()["next_runs"]), 3)
            created = client.post("/api/review-schedules", json=body)
            self.assertEqual(created.status_code, 201)
            saved = created.json()
            endpoint = f"/api/review-schedules/{saved['id']}"
            response = client.get("/api/review-schedules")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json()["schedules"][0], saved)
            self.assertEqual(client.post("/api/review-schedules", json={**body, "cron": "0 9 31 2 *"}).status_code, 422)
            self.assertEqual(client.put(endpoint, json={**body, "revision": 1, "enabled": False}).status_code, 200)
            self.assertEqual(client.put(endpoint, json={**body, "revision": 1}).status_code, 409)
            with patch("app.snapshot_analysis_api.vision_configured", return_value=False):
                self.assertEqual(client.put(endpoint, json={**body, "revision": 2}).status_code, 503)
                self.assertEqual(client.put(endpoint, json={**body, "revision": 2, "enabled": False}).status_code, 200)
            self.assertEqual(client.delete(endpoint + "?revision=1").status_code, 409)
            self.assertEqual(client.delete(endpoint + "?revision=3").json(), {"deleted": True})
            self.assertEqual(client.get("/api/review-schedules").json()["schedules"], [])
            capture.assert_not_called()

    def test_schedule_api_authentication_and_csrf(self):
        endpoint = "/api/review-schedules"
        with TestClient(app) as client:
            self.assertEqual(client.get(endpoint).status_code, 401)
            for method, url in (("POST", endpoint), ("POST", endpoint + "/preview"),
                                ("PUT", endpoint + f"/{uuid4()}"), ("DELETE", endpoint + f"/{uuid4()}?revision=1")):
                self.assertEqual(client.request(method, url, json={}).status_code, 403)
                self.assertEqual(client.request(method, url, json={}, headers={"X-Requested-With": "XMLHttpRequest"}).status_code, 401)
