from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pyodbc

from app import snapshot_analysis as analysis, snapshot_batches as batches, scheduled_inspection as scheduled
from app.auth import require_user
from app.inspection import ScreenshotAnalysis
from app.main import app
from app.recognition_models import RecognitionCheck
from app.recorder import RecorderSettings
from app.vision_analysis import AnalysisError, RetryableAnalysisError


@patch.dict(os.environ, {"SNAPSHOT_ANALYSIS_WORKER_ENABLED": "false"})
class SnapshotAnalysisTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.queue = analysis.AnalysisQueue(self.root)
        buffer = BytesIO()
        Image.new("RGB", (32, 24), "gray").save(buffer, "JPEG")
        self.image = buffer.getvalue()
        self.captured = datetime(2026, 11, 26, 1, 30, tzinfo=timezone.utc)
        self.source = f"batch:{uuid4()}"
        self.owner = str(uuid4())
        self.result = ScreenshotAnalysis(
            evidence=self.image, width=32, height=24, analyzer_version="vision-llm-v2",
            checks=[RecognitionCheck(category=category, status="flagged", explanation="Test evidence")
                    for category in ("signal", "timestamp", "scene")],
        )
        for target, result in (
            ("repository.get_record", None), ("repository.recorder_identifier", "stable-recorder"),
            ("repository.get_baseline_evidence", b"approved-baseline"), ("repository.save_record", None),
            ("analyze_screenshot", self.result),
        ):
            mock = patch(f"app.snapshot_analysis.{target}", return_value=result)
            self.addCleanup(mock.stop)
            setattr(self, target.split(".")[-1], mock.start())
        self.queue.lead(self.owner, time.time())

    def enqueue(self):
        item_id = uuid4()
        path = self.root / f"{item_id}.jpeg"
        path.write_bytes(self.image)
        self.queue.enqueue(self.source, item_id, path, "001", "Hong Kong", 1, self.captured,
                           recorder_key="ab" * 32)
        return item_id, path

    def process(self):
        row = self.queue.claim(self.owner)
        self.assertIsNotNone(row)
        analysis.process_item(self.queue, row)
        return row

    def test_saved_image_time_and_stable_baseline_are_reused_without_recapture(self):
        item_id, path = self.enqueue()
        self.process()
        self.analyze_screenshot.assert_called_once_with(self.image, self.captured, b"approved-baseline")
        self.get_baseline_evidence.assert_called_once_with("Hong Kong", "001", "Channel 1 [stable-recorder]")
        record, image = self.save_record.call_args.args
        self.assertEqual((record.id, record.captured_at, image), (item_id, self.captured, self.image))
        reopened = analysis.AnalysisQueue(self.root)
        reopened.enqueue(self.source, item_id, path, "001", "Hong Kong", 1, self.captured, recorder_key="ab" * 32)
        reopened.retry_failed(self.source)
        self.assertIsNone(reopened.claim(self.owner))
        report = reopened.page(self.source)
        self.assertEqual((report.summary.total, report.summary.completed, report.summary.flagged), (1, 1, 1))
        self.assertEqual(report.items[0].record.id, item_id)
        self.assertEqual(report.items[0].record.captured_at, self.captured)

    def test_sql_failure_reuses_cached_analysis_on_retry(self):
        self.enqueue()
        self.save_record.side_effect = pyodbc.OperationalError("test database outage")
        start = time.time()
        for index in range(3):
            with patch("app.snapshot_analysis.time.time", return_value=start + index * 30):
                self.queue.lead(self.owner, start + index * 30)
                self.process()
        self.assertEqual(self.queue.page(self.source).summary.failed, 1)
        self.analyze_screenshot.assert_called_once()
        self.save_record.side_effect = None
        self.queue.retry_failed(self.source)
        self.process()
        self.assertEqual(self.queue.page(self.source).summary.completed, 1)
        self.analyze_screenshot.assert_called_once()

    def test_expired_worker_recovers_cached_work_and_does_not_repeat_completed_jobs(self):
        self.enqueue()
        self.process()
        self.enqueue()
        row = self.queue.claim(self.owner)
        self.save_record.side_effect = pyodbc.OperationalError("test outage")
        analysis.process_item(self.queue, row)
        with self.queue.connection() as db:
            db.execute("UPDATE jobs SET status='analyzing' WHERE id=?", (row["id"],))
        reopened = analysis.AnalysisQueue(self.root)
        self.assertFalse(reopened.lead("replacement", time.time()))
        self.assertTrue(reopened.lead("replacement", time.time() + analysis.LEASE_SECONDS + 1))
        self.save_record.side_effect = None
        with self.queue.connection() as db:
            db.execute("UPDATE jobs SET available=0")
        resumed = reopened.claim("replacement")
        analysis.process_item(reopened, resumed)
        self.assertEqual(reopened.page(self.source).summary.completed, 2)
        self.assertEqual(self.analyze_screenshot.call_count, 2)
        self.assertIsNone(reopened.claim("replacement"))

    def test_retry_after_pauses_queue_and_transient_retries_are_bounded(self):
        self.enqueue()
        self.analyze_screenshot.side_effect = RetryableAnalysisError("HTTP 429", 120, True)
        start = time.time()
        for index in range(3):
            now = start + 121 * index
            with patch("app.snapshot_analysis.time.time", return_value=now):
                self.queue.lead(self.owner, now)
                self.process()
                self.assertIsNone(self.queue.claim(self.owner))
        self.assertEqual(self.analyze_screenshot.call_count, 3)
        self.assertEqual(self.queue.page(self.source).summary.failed, 1)

    def test_permanent_failure_retry_preserves_other_states(self):
        completed, _ = self.enqueue()
        self.process()
        failed, _ = self.enqueue()
        self.analyze_screenshot.side_effect = AnalysisError("Model rejected the image.")
        self.process()
        queued, _ = self.enqueue()
        self.queue.retry_failed(self.source)
        states = {item.id: item.status for item in self.queue.page(self.source).items}
        self.assertEqual(states, {completed: "completed", failed: "queued", queued: "queued"})
        self.assertEqual(self.queue.page(self.source, status="completed").total, 1)

    def test_changed_or_missing_images_fail_before_any_model_call(self):
        for changed in (True, False):
            _, path = self.enqueue()
            if changed:
                path.write_bytes(b"modified")
            else:
                path.unlink()
            self.process()
        self.analyze_screenshot.assert_not_called()
        self.assertEqual(self.queue.page(self.source).summary.failed, 2)
        with self.assertRaises(analysis.QueueError):
            self.queue.enqueue(self.source, uuid4(), self.root.parent / "outside.jpeg",
                               "001", "Hong Kong", 1, self.captured)

    def test_multiple_workers_share_concurrency_bound(self):
        for _ in range(4):
            self.enqueue()
        self.queue.release(self.owner)
        entered, release, finished = Event(), Event(), Event()
        lock = Lock()
        active = maximum = count = 0

        def model(*args):
            nonlocal active, maximum, count
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release model.")
            with lock:
                active -= 1
                count += 1
                if count == 4:
                    finished.set()
            return self.result

        self.analyze_screenshot.side_effect = model
        first = analysis.AnalysisWorker(self.root, 2)
        second = analysis.AnalysisWorker(self.root, 4)
        first.start()
        try:
            self.assertTrue(entered.wait(5))
            second.start()
            release.set()
            self.assertTrue(finished.wait(5))
        finally:
            release.set()
            first.stop()
            if second.thread.ident is not None:
                second.stop()
        self.assertEqual((maximum, count), (2, 4))
        self.assertEqual(self.queue.page(self.source).summary.completed, 4)

    def api_setup(self, quantity=26):
        settings = RecorderSettings(_env_file=None, snapshot_dir=self.root, dahau_password="test-only")
        for target, result in (
            ("app.snapshot_batches.RecorderSettings", settings),
            ("app.snapshot_analysis_api.RecorderSettings", settings),
            ("app.snapshot_batches._eligible_recorders", [("001", "Hong Kong", "192.0.2.1:81", quantity)]),
            ("app.snapshot_analysis_api.vision_configured", True),
        ):
            mock = patch(target, return_value=result)
            self.addCleanup(mock.stop)
            mock.start()
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        report, _ = batches._plan_batch("test-only", self.root)
        for item in report.items:
            item.status = "completed"
            item.captured_at = self.captured
            item.has_evidence = True
            path = batches._evidence_path(self.root, report, item)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.image)
        report.status = "completed"
        batches._write_report(self.root, report)
        return report

    def test_api_pilot_then_all_idempotency_reports_and_settings(self):
        report = self.api_setup()
        base = f"/api/snapshot-analysis/batch/{report.id}"
        with TestClient(app) as client, patch("app.snapshot_batches.Recorder.snapshot") as snapshot:
            endpoint = f"/api/snapshot-batches/{report.id}/analysis"
            for _ in range(2):
                response = client.post(endpoint, json={"limit": 20})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["summary"]["total"], 20)
            response = client.post(endpoint, json={"limit": None})
            self.assertEqual(response.json()["summary"]["total"], 26)
            page = client.get(base + "?page=2&page_size=20").json()
            self.assertEqual((len(page["items"]), page["summary"]["queued"]), (6, 26))
            self.assertEqual(client.get(base + "?status=failed").json()["total"], 0)
            exported = client.get(base + "/report")
            self.assertEqual(len(exported.json()["items"]), 26)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual(exported.headers["cache-control"], "no-store")
            self.assertNotIn("192.0.2", exported.text)
            self.assertNotIn("path", exported.text)
            self.assertEqual(client.patch("/api/snapshot-analysis/settings", json={"auto_analyze": True}).status_code, 200)
            self.assertTrue(analysis.AnalysisQueue(self.root).auto_analyze())
            self.assertEqual(client.post(base + "/retry").status_code, 202)
            self.assertEqual(client.post(endpoint, json={"limit": 100000}).status_code, 422)
            with patch("app.snapshot_analysis_api.vision_configured", return_value=False):
                self.assertEqual(client.post(endpoint, json={"limit": 20}).status_code, 503)
                self.assertEqual(client.patch("/api/snapshot-analysis/settings", json={"auto_analyze": False}).status_code, 200)
            snapshot.assert_not_called()
        self.analyze_screenshot.assert_not_called()

    def test_authentication_and_csrf_protect_queue_mutations(self):
        report = self.api_setup()
        app.dependency_overrides.pop(require_user)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/snapshot-analysis/settings").status_code, 401)
            for endpoint in (f"/api/snapshot-batches/{report.id}/analysis",
                             f"/api/snapshot-analysis/batch/{report.id}/retry"):
                self.assertEqual(client.post(endpoint, json={}).status_code, 403)
                self.assertEqual(client.post(endpoint, json={}, headers={"X-Requested-With": "XMLHttpRequest"}).status_code, 401)
            self.assertEqual(client.patch("/api/snapshot-analysis/settings", json={"auto_analyze": True}).status_code, 403)

    def test_manual_auto_enqueue_failure_does_not_fail_capture(self):
        report = self.api_setup(2)
        for item in report.items:
            item.status = "pending"
            item.has_evidence = False
        report.auto_analyze = True
        recorder = MagicMock()
        recorder.snapshot.return_value = self.image
        with patch("app.snapshot_analysis.AnalysisQueue.enqueue", side_effect=analysis.QueueError("Queue unavailable")):
            batches._capture_batch(self.root, report, {1: recorder}, MagicMock())
        self.assertEqual((report.status, report.summary().completed, report.summary().saved), ("completed", 2, 2))
        self.assertEqual(report.analysis_error, "Queue unavailable")
        self.assertFalse(report.error)

    def test_auto_setting_enqueues_new_capture_and_results_are_independent(self):
        self.api_setup(2)
        self.queue.set_auto_analyze(True)
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot", return_value=self.image,
        ), patch("app.snapshot_batches._acquire_lock", return_value=MagicMock()):
            with patch("app.snapshot_analysis_api.vision_configured", return_value=False):
                response = client.post("/api/snapshot-batches")
            self.assertEqual(response.status_code, 202)
            batch_id = response.json()["id"]
            source = f"batch:{batch_id}"
            self.assertEqual(self.queue.page(source).summary.queued, 2)
            self.analyze_screenshot.assert_not_called()
            self.process()
            result = client.get(f"/api/snapshot-analysis/batch/{batch_id}").json()
            self.assertEqual((result["summary"]["completed"], result["summary"]["queued"]), (1, 1))
            capture = batches._read_report(self.root, response.json()["id"])
            self.assertEqual((capture.summary().completed, capture.summary().failed), (2, 0))

    def test_sql_committed_before_worker_restart_is_not_analyzed_again(self):
        _, path = self.enqueue()
        self.process()
        record = self.save_record.call_args.args[0]
        with self.queue.connection() as db:
            db.execute("UPDATE jobs SET status='queued', result=NULL")
        self.get_record.return_value = record
        path.unlink()
        self.process()
        self.analyze_screenshot.assert_called_once()
        self.save_record.assert_called_once()
        self.assertEqual(self.queue.page(self.source).summary.completed, 1)

    def test_scheduled_analysis_queues_without_running_model_and_keeps_capture_status(self):
        recorder = MagicMock()
        recorder.base = "http://192.0.2.1:81"
        recorder.snapshot.return_value = self.image
        item_id = uuid4()
        day = self.captured.date()
        with patch("app.scheduled_inspection._execute") as execute:
            scheduled._capture_channels(recorder, "stable", "001", "Hong Kong",
                                        [(1, "Channel 1 [stable]", item_id)], day, self.root, True)
        self.assertEqual(execute.call_args.args[1], "completed")
        page = self.queue.page(f"scheduled:{day}")
        self.assertEqual((page.summary.queued, page.items[0].id), (1, item_id))
        self.analyze_screenshot.assert_not_called()
        self.save_record.assert_not_called()
