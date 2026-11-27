from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError
from uuid import uuid4

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from PIL import Image

from app import snapshot_batches as batches
from app.auth import require_user
from app.channel_exclusions import ChannelExclusions, recorder_key
from app.main import app
from app.recorder import Recorder, RecorderAuthenticationError, RecorderChannelNotFoundError, RecorderError, RecorderSettings
from app import scheduled_inspection as worker
from app.snapshot_storage import SnapshotStorageError


class SnapshotBatchesTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        settings = RecorderSettings(_env_file=None, snapshot_dir=self.root, dahau_password="test-only")
        for target, value in (
            ("RecorderSettings", settings),
            ("_acquire_lock", MagicMock()),
            ("_eligible_recorders", [("001", "Hong Kong", "192.0.2.1:81", 3)]),
        ):
            mock = patch(f"app.snapshot_batches.{target}", return_value=value)
            self.addCleanup(mock.stop)
            setattr(self, target, mock.start())
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        image = BytesIO()
        Image.new("RGB", (4, 3)).save(image, format="PNG")
        self.image = image.getvalue()

    def test_batch_saves_jpeg_reports_missing_channel_and_keeps_repeated_runs(self) -> None:
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot",
            side_effect=[self.image, RecorderError("Recorder returned HTTP 404."), self.image] * 2,
        ) as snapshot:
            ids = []
            for _ in range(2):
                response = client.post("/api/snapshot-batches")
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["total"], 3)
                batch_id = response.json()["id"]
                ids.append(batch_id)
                runs = client.get("/api/snapshot-batches").json()
                run = next(run for run in runs if run["id"] == batch_id)
                self.assertEqual((run["status"], run["completed"], run["failed"], run["saved"]),
                                 ("completed", 2, 1, 2))
                items = client.get(f"/api/snapshot-batches/{batch_id}/items").json()
                self.assertEqual([item["status"] for item in items["items"]], ["completed", "failed", "completed"])
                self.assertIn("404", items["items"][1]["message"])
                first = items["items"][0]
                evidence = client.get(f"/api/snapshot-batches/{batch_id}/items/{first['id']}/evidence")
                self.assertEqual(evidence.status_code, 200)
                self.assertEqual(evidence.headers["content-type"], "image/jpeg")
                with Image.open(BytesIO(evidence.content)) as saved:
                    self.assertEqual(saved.format, "JPEG")
                exported = client.get(f"/api/snapshot-batches/{batch_id}/report")
                self.assertEqual(exported.json()["status"], "completed")
                self.assertNotIn("192.0.2", exported.text)
                self.assertNotIn("test-only", exported.text)
                self.assertIn("attachment", exported.headers["content-disposition"])
                missing = items["items"][1]["id"]
                self.assertEqual(client.get(f"/api/snapshot-batches/{batch_id}/items/{missing}/evidence").status_code, 404)
            self.assertNotEqual(*ids)
            self.assertEqual(snapshot.call_args_list, [call(1), call(2), call(3)] * 2)
        self.assertEqual(len(list(self.root.rglob("*.jpeg"))), 4)
        self.assertEqual(len(list(self.root.rglob("report.json"))), 2)

    def test_progress_is_visible_while_capture_runs_and_total_is_stable(self) -> None:
        entered, release = Event(), Event()
        def capture(channel):
            if channel == 2:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Test did not release capture.")
            return self.image

        report, recorders = batches._plan_batch("test-only")
        batches._write_report(self.root, report)
        with patch("app.snapshot_batches.Recorder.snapshot", side_effect=capture), ThreadPoolExecutor() as pool:
            future = pool.submit(batches._capture_batch, self.root, report, recorders, MagicMock())
            try:
                self.assertTrue(entered.wait(5))
                with TestClient(app) as client, patch("app.snapshot_batches._acquire_lock", return_value=None):
                    run = client.get("/api/snapshot-batches").json()[0]
                    self.assertEqual((run["total"], run["completed"], run["saved"]), (3, 1, 1))
                    items = client.get(f"/api/snapshot-batches/{report.id}/items").json()["items"]
                    self.assertEqual([item["status"] for item in items], ["completed", "capturing", "pending"])
                    self.assertEqual(client.post("/api/snapshot-batches").status_code, 409)
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(batches._read_report(self.root, report.id).summary().completed, 3)

    def test_stop_one_brand_finishes_active_capture_and_continues_other_brand(self) -> None:
        self._eligible_recorders.return_value = [
            ("001", "Hong Kong", "192.0.2.1:81", 2, "DAHAU", "admin", "test-only"),
            ("002", "Hong Kong", "192.0.2.2:81", 2, "HIKVISION", "admin", "test-only"),
        ]
        for stopped_brands, expected in (
            (("DAHAU",), [("DAHAU", 1), ("HIKVISION", 1), ("HIKVISION", 2)]),
            (("HIKVISION",), [("DAHAU", 1), ("DAHAU", 2)]),
            (("DAHAU", "HIKVISION"), [("DAHAU", 1)]),
        ):
            with self.subTest(brands=stopped_brands):
                entered, release = Event(), Event()
                calls = []

                def capture(recorder, channel):
                    calls.append((recorder.brand, channel))
                    if len(calls) == 1:
                        entered.set()
                        if not release.wait(5):
                            raise TimeoutError("Test did not release capture.")
                    return self.image

                report, recorders = batches._plan_batch("test-only", self.root)
                batches._write_report(self.root, report)
                with patch("app.recorder.Recorder.snapshot", new=capture), ThreadPoolExecutor() as pool:
                    future = pool.submit(batches._capture_batch, self.root, report, recorders, MagicMock())
                    try:
                        self.assertTrue(entered.wait(5))
                        with TestClient(app) as client, patch("app.snapshot_batches._acquire_lock", return_value=None):
                            for brand in stopped_brands:
                                response = client.post(f"/api/snapshot-batches/{report.id}/stop/{brand}")
                                self.assertEqual(response.status_code, 202)
                                self.assertIn(brand, response.json()["stop_requested"])
                            self.assertEqual(batches._read_report(self.root, report.id).items[0].status, "capturing")
                            self.assertEqual(client.post(f"/api/snapshot-batches/{report.id}/stop/other").status_code, 422)
                    finally:
                        release.set()
                    future.result(timeout=5)
                result = batches._read_report(self.root, report.id)
                self.assertEqual(calls, expected)
                self.assertEqual(result.status, "stopped")
                self.assertEqual(result.items[0].status, "completed")
                self.assertEqual(result.summary().stopped, 4 - len(expected))

    def test_auth_failure_does_not_retry_rejected_recorder_and_continues_next(self) -> None:
        self._eligible_recorders.return_value.append(("002", "Macau", "192.0.2.2", 1))
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot",
            side_effect=[RecorderAuthenticationError("Recorder authentication failed."), self.image],
        ) as snapshot:
            batch_id = client.post("/api/snapshot-batches").json()["id"]
            items = client.get(f"/api/snapshot-batches/{batch_id}/items").json()["items"]
        self.assertEqual(snapshot.call_args_list, [call(1), call(1)])
        self.assertEqual([item["status"] for item in items], ["failed", "failed", "failed", "completed"])
        self.assertIn("Not attempted", items[1]["message"])

    def test_batch_captures_dahua_and_hikvision_with_their_own_credentials(self) -> None:
        self._eligible_recorders.return_value = [
            ("001", "Hong Kong", "192.0.2.1:81", 1, "DAHAU", "admin", "dahua-test"),
            ("002", "Hong Kong", "192.0.2.2:81", 2, "HIKVISION", "operator", "hik-test"),
        ]
        requests = []

        def capture(recorder, path, limit):
            requests.append((recorder.brand, recorder.base, path))
            return self.image

        with TestClient(app) as client, patch(
            "app.recorder.Recorder._get", autospec=True, side_effect=capture,
        ), patch("app.recorder.HTTPPasswordMgrWithDefaultRealm") as manager:
            response = client.post("/api/snapshot-batches")
            self.assertEqual(response.status_code, 202)
            items = client.get(f"/api/snapshot-batches/{response.json()['id']}/items").json()["items"]
        self.assertEqual([item["status"] for item in items], ["completed"] * 3)
        self.assertEqual([entry[2] for entry in requests], [
            "/cgi-bin/snapshot.cgi?channel=1",
            "/ISAPI/Streaming/channels/101/picture",
            "/ISAPI/Streaming/channels/201/picture",
        ])
        self.assertEqual([entry[0] for entry in requests], ["DAHAU", "HIKVISION", "HIKVISION"])
        self.assertEqual(manager.return_value.add_password.call_args_list, [
            call(None, "http://192.0.2.1:81", "admin", "dahua-test"),
            call(None, "http://192.0.2.2:81", "operator", "hik-test"),
        ])

    def test_manual_batch_brand_filter_limits_capture_and_rejects_invalid_values(self) -> None:
        rows = [
            ("001", "Hong Kong", "192.0.2.1:81", 1, "DAHAU", "admin", "test-only"),
            ("002", "Hong Kong", "192.0.2.2:81", 1, "HIKVISION", "admin", "test-only"),
        ]
        def select_recorders(*, include_missing_addresses, brand=None):
            return [row for row in rows if brand is None or row[4] == brand]

        self._eligible_recorders.side_effect = select_recorders
        captured = []
        def capture(recorder, channel):
            captured.append((recorder.brand, channel))
            return self.image

        with TestClient(app) as client, patch("app.recorder.Recorder.snapshot", new=capture):
            for query, expected in (
                ("brand=DAHAU", ["DAHAU"]),
                ("brand=HIKVISION", ["HIKVISION"]),
                ("", ["DAHAU", "HIKVISION"]),
            ):
                captured.clear()
                response = client.post(f"/api/snapshot-batches?{query}")
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["total"], len(expected))
                self.assertEqual([brand for brand, _ in captured], expected)
            self._acquire_lock.reset_mock()
            self.assertEqual(client.post("/api/snapshot-batches?brand=other").status_code, 422)
            self._acquire_lock.assert_not_called()
            rows[:] = [row for row in rows if row[4] == "DAHAU"]
            response = client.post("/api/snapshot-batches?brand=HIKVISION")
            self.assertEqual(response.status_code, 422)
            self.assertIn("selected brand", response.json()["detail"])

    def test_archive_failure_is_reported_without_claiming_saved_image(self) -> None:
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot", return_value=self.image,
        ), patch("app.snapshot_batches.save_snapshot", side_effect=SnapshotStorageError("Archive write failed.")):
            batch_id = client.post("/api/snapshot-batches").json()["id"]
            run = client.get("/api/snapshot-batches").json()[0]
            self.assertEqual((run["failed"], run["saved"]), (3, 0))
            report = client.get(f"/api/snapshot-batches/{batch_id}/report").json()
            self.assertTrue(all(item["message"] == "Archive write failed." for item in report["items"]))

    def test_incomplete_report_is_recovered_only_when_no_backend_holds_lock(self) -> None:
        report, _ = batches._plan_batch("test-only")
        batches._write_report(self.root, report)
        with TestClient(app) as client:
            with patch("app.snapshot_batches._acquire_lock", return_value=None):
                self.assertEqual(client.get("/api/snapshot-batches").json()[0]["status"], "running")
            run = client.get("/api/snapshot-batches").json()[0]
            self.assertEqual((run["status"], run["interrupted"]), ("interrupted", 3))
        self.assertEqual(batches._read_report(self.root, report.id).status, "interrupted")

    @unittest.skipUnless(sys.platform == "win32", "Windows report sharing semantics")
    def test_report_reader_does_not_abort_concurrent_progress_update(self) -> None:
        report, _ = batches._plan_batch("test-only")
        batches._write_report(self.root, report)
        destination = self.root / "batches" / str(report.id) / "report.json"
        report.items[0].status = "failed"
        blocked = Event()
        replace = Path.replace

        def observe_replacement(source, target):
            try:
                return replace(source, target)
            except PermissionError:
                blocked.set()
                raise

        with patch.object(Path, "replace", observe_replacement), ThreadPoolExecutor() as pool:
            with destination.open("r"):
                future = pool.submit(batches._write_report, self.root, report)
                self.assertTrue(blocked.wait(2), "The reader should temporarily block replacement on Windows.")
            future.result(timeout=3)
        self.assertEqual(batches._read_report(self.root, report.id).items[0].status, "failed")
        self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_persistent_report_permission_failure_is_bounded_and_preserves_previous_report(self) -> None:
        report, _ = batches._plan_batch("test-only")
        batches._write_report(self.root, report)
        report.items[0].status = "completed"
        error = PermissionError(13, "Access denied")
        error.winerror = 5
        with patch.object(Path, "replace", side_effect=error) as replace, patch(
            "app.snapshot_batches.clock.sleep",
        ) as sleep:
            with self.assertRaises(PermissionError):
                batches._write_report(self.root, report)
        self.assertEqual((replace.call_count, sleep.call_count), (6, 5))
        self.assertEqual(batches._read_report(self.root, report.id).items[0].status, "pending")
        self.assertEqual(list((self.root / "batches" / str(report.id)).glob("*.tmp")), [])

    def test_unexpected_report_failure_persists_safe_diagnostic_for_the_ui(self) -> None:
        report, recorders = batches._plan_batch("test-only")
        batches._write_report(self.root, report)
        error = PermissionError(13, "Private filesystem details")
        error.winerror = 32
        write = batches._write_report
        writes = 0
        def fail_first_write(root, batch):
            nonlocal writes
            writes += 1
            if writes == 1:
                raise error
            write(root, batch)

        with patch("app.snapshot_batches._write_report", side_effect=fail_first_write), self.assertLogs(
            "app.snapshot_batches", level="ERROR",
        ):
            batches._capture_batch(self.root, report, recorders, MagicMock())
        saved = batches._read_report(self.root, report.id)
        self.assertEqual(saved.summary().interrupted, 3)
        self.assertIn("Windows error 32", saved.summary().error)
        self.assertNotIn("Private filesystem details", saved.model_dump_json())
        self.assertTrue(all("Windows error 32" in item.message for item in saved.items))

    def test_planning_rejects_bad_counts_and_duplicates_before_capture(self) -> None:
        self._eligible_recorders.return_value.extend([
            ("001", "Hong Kong", "192.0.2.1:81", 3),
            ("002", "Macau", "192.0.2.2", 0),
        ])
        report, recorders = batches._plan_batch("test-only")
        self.assertEqual((len(report.items), len(recorders)), (5, 1))
        self.assertEqual([item.status for item in report.items], ["pending"] * 3 + ["skipped"] * 2)

    def test_missing_addresses_are_reported_and_do_not_block_other_recorders(self) -> None:
        self._eligible_recorders.return_value.extend([
            ("002", "Macau", None, 2),
            ("003", "Hong Kong", "", 4),
            ("004", "Hong Kong", "   ", 1),
        ])
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot", return_value=self.image,
        ) as snapshot:
            batch_id = client.post("/api/snapshot-batches").json()["id"]
            report = client.get(f"/api/snapshot-batches/{batch_id}/report").json()
            run = client.get("/api/snapshot-batches").json()[0]
        self._eligible_recorders.assert_called_once_with(include_missing_addresses=True)
        self.assertEqual((run["total"], run["completed"], run["skipped"]), (6, 3, 3))
        missing = [item for item in report["items"] if item["shop_code"] != "001"]
        self.assertEqual([item["shop_code"] for item in missing], ["002", "003", "004"])
        self.assertTrue(all(item["status"] == "skipped" and "IP address is missing" in item["message"]
                            for item in missing))
        self.assertEqual(snapshot.call_args_list, [call(1), call(2), call(3)])

    def test_empty_configuration_and_missing_password_do_not_start_background_capture(self) -> None:
        self._eligible_recorders.return_value = []
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/snapshot-batches").status_code, 422)
            self.assertFalse((self.root / "batches").exists())
            self.RecorderSettings.return_value = RecorderSettings(_env_file=None, dahau_password="")
            self._acquire_lock.reset_mock()
            self.assertEqual(client.post("/api/snapshot-batches").status_code, 503)
            self._acquire_lock.assert_not_called()

    def test_user_exclusion_persists_skips_network_requests_and_can_be_reenabled(self) -> None:
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot", return_value=self.image,
        ) as snapshot:
            batch_id = client.post("/api/snapshot-batches").json()["id"]
            items = client.get(f"/api/snapshot-batches/{batch_id}/items").json()["items"]
            channel = items[1]
            url = f"/api/snapshot-batches/{batch_id}/items/{channel['id']}/exclusion"
            self.assertEqual(client.patch(url, json={"skip": True}).status_code, 200)
            # The next run opens the persisted store again, as a restarted backend would.
            key = recorder_key("001", "Hong Kong", "http://192.0.2.1:81")
            self.assertEqual(ChannelExclusions(self.root).get(key), {2: "user"})
            snapshot.reset_mock()
            next_id = client.post("/api/snapshot-batches").json()["id"]
            next_items = client.get(f"/api/snapshot-batches/{next_id}/items").json()["items"]
            self.assertEqual(snapshot.call_args_list, [call(1), call(3)])
            self.assertEqual(next_items[1]["status"], "skipped")
            self.assertTrue(next_items[1]["excluded"])
            self.assertIn("excluded by a user", next_items[1]["message"])
            self.assertEqual(client.patch(url, json={"skip": False}).status_code, 200)
            snapshot.reset_mock()
            client.post("/api/snapshot-batches")
            self.assertEqual(snapshot.call_args_list, [call(1), call(2), call(3)])

    def test_404_is_learned_only_on_working_recorder_and_recheck_rediscovers_channel(self) -> None:
        with TestClient(app) as client, patch("app.snapshot_batches.Recorder.snapshot") as snapshot:
            snapshot.side_effect = [self.image, RecorderChannelNotFoundError("Recorder returned HTTP 404."),
                                    RecorderError("Recorder request failed or timed out.")]
            client.post("/api/snapshot-batches")
            key = recorder_key("001", "Hong Kong", "http://192.0.2.1:81")
            self.assertEqual(ChannelExclusions(self.root).get(key), {2: "not_found"})
            snapshot.reset_mock()
            snapshot.side_effect = None
            snapshot.return_value = self.image
            client.post("/api/snapshot-batches")
            self.assertEqual(snapshot.call_args_list, [call(1), call(3)])
            snapshot.reset_mock()
            client.post("/api/snapshot-batches?recheck_skipped=true")
            self.assertEqual(snapshot.call_args_list, [call(1), call(2), call(3)])
            self.assertEqual(ChannelExclusions(self.root).get(key), {})
            ChannelExclusions(self.root).set_skipped(key, 2, True)
            snapshot.reset_mock()
            client.post("/api/snapshot-batches?recheck_skipped=true")
            self.assertEqual(snapshot.call_args_list, [call(1), call(2), call(3)])
            self.assertEqual(ChannelExclusions(self.root).get(key), {})

    def test_unsupported_snapshot_endpoint_and_bad_requests_are_not_learned_as_missing(self) -> None:
        key = recorder_key("001", "Hong Kong", "http://192.0.2.1:81")
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot",
            side_effect=RecorderChannelNotFoundError("Recorder returned HTTP 404."),
        ):
            client.post("/api/snapshot-batches")
        self.assertEqual(ChannelExclusions(self.root).get(key), {})
        with TestClient(app) as client, patch(
            "app.snapshot_batches.Recorder.snapshot",
            side_effect=[self.image, RecorderError("Recorder returned HTTP 400."), self.image],
        ):
            client.post("/api/snapshot-batches")
        self.assertEqual(ChannelExclusions(self.root).get(key), {})

    def test_legacy_report_exclusion_requires_unique_current_recorder_and_follows_ip_identity(self) -> None:
        report, _ = batches._plan_batch("test-only")
        report.recorder_keys = {}
        report.status = "completed"
        batches._write_report(self.root, report)
        url = f"/api/snapshot-batches/{report.id}/items/{report.items[1].id}/exclusion"
        with TestClient(app) as client:
            self._eligible_recorders.return_value.append(("001", "Hong Kong", "192.0.2.2", 3))
            self.assertEqual(client.patch(url, json={"skip": True}).status_code, 409)
            self._eligible_recorders.return_value.pop()
            self.assertEqual(client.patch(url, json={"skip": True}).status_code, 200)
            self._eligible_recorders.return_value = [("001", "Hong Kong", "192.0.2.99", 3)]
            with patch("app.snapshot_batches.Recorder.snapshot", return_value=self.image) as snapshot:
                client.post("/api/snapshot-batches")
            self.assertEqual(snapshot.call_args_list, [call(1), call(2), call(3)])

    def test_legacy_report_resolves_current_rows_with_brand_and_credentials(self) -> None:
        report, _ = batches._plan_batch("test-only")
        report.recorder_keys = {}
        report.status = "completed"
        batches._write_report(self.root, report)
        self._eligible_recorders.return_value = [
            ("001", "Hong Kong", "192.0.2.1:81", 3, "HIKVISION", "admin", "test-only"),
        ]
        with TestClient(app) as client:
            response = client.get(f"/api/snapshot-batches/{report.id}/items")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 3)
            self.assertTrue(all(item["can_exclude"] for item in response.json()["items"]))
            item_id = report.items[0].id
            self.assertEqual(client.patch(
                f"/api/snapshot-batches/{report.id}/items/{item_id}/exclusion", json={"skip": True},
            ).status_code, 200)

    def test_scheduled_capture_respects_manual_exclusions_without_contacting_disabled_channel(self) -> None:
        key = recorder_key("001", "Hong Kong", "http://192.0.2.1:81")
        ChannelExclusions(self.root).set_skipped(key, 2, True)
        report_date = batches._utc_now().date()
        with patch("app.scheduled_inspection._recorder_identifier", return_value=str(uuid4())), patch(
            "app.scheduled_inspection._execute",
        ), patch("app.scheduled_inspection._add_item", side_effect=lambda *args: uuid4()) as add, patch(
            "app.scheduled_inspection.Recorder.snapshot", return_value=self.image,
        ) as snapshot:
            worker._process_recorder(
                report_date, 1, ("001", "Hong Kong", "192.0.2.1:81", 3),
                set(), self.root, "test-only", False,
            )
        self.assertEqual(snapshot.call_args_list, [call(1), call(3)])
        self.assertEqual(add.call_args_list[1].args[-3:-1], ("skipped", "excluded"))
        self.assertTrue(all(args.args[0] == report_date for args in add.call_args_list))

    def test_recorder_distinguishes_http_404_from_transient_errors(self) -> None:
        recorder = Recorder("192.0.2.1", "admin", "test-only")
        with patch.object(recorder.opener, "open", side_effect=HTTPError(
            recorder.base, 404, "Not found", {}, None,
        )):
            with self.assertRaises(RecorderChannelNotFoundError):
                recorder.snapshot(2)

    def test_start_releases_session_lock_after_planning_failure(self) -> None:
        self._eligible_recorders.side_effect = RuntimeError("Unexpected planning error")
        connection = self._acquire_lock.return_value
        with self.assertRaises(RuntimeError):
            batches.start_batch(BackgroundTasks())
        self.assertIn("sp_releaseapplock", connection.cursor.return_value.execute.call_args.args[0])
        connection.close.assert_called_once()

    def test_requires_authentication_and_csrf_header(self) -> None:
        app.dependency_overrides.pop(require_user)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/snapshot-batches").status_code, 401)
            self.assertEqual(client.post("/api/snapshot-batches").status_code, 403)
            self.assertEqual(client.post("/api/snapshot-batches", headers={
                "X-Requested-With": "XMLHttpRequest",
            }).status_code, 401)
            path = f"/api/snapshot-batches/{uuid4()}/items/{uuid4()}/exclusion"
            self.assertEqual(client.patch(path, json={"skip": True}).status_code, 403)
            self.assertEqual(client.patch(path, json={"skip": True}, headers={
                "X-Requested-With": "XMLHttpRequest",
            }).status_code, 401)
        self._acquire_lock.assert_not_called()
