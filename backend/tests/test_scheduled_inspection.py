from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import Mock, call, patch
from uuid import uuid4

from PIL import Image

from app import scheduled_inspection as worker
from app.recorder import Recorder, RecorderAuthenticationError, RecorderSettings


class ScheduledInspectionTest(unittest.TestCase):
    def test_worker_starts_at_0930_on_working_days_only(self) -> None:
        fake_calendar = ModuleType("holidays")
        calendar_factory = Mock(return_value={date(2026, 12, 25)})
        setattr(fake_calendar, "country_holidays", calendar_factory)
        polls = [
            datetime(2026, 12, 24, 9, 29, 59, tzinfo=worker.HONG_KONG),
            datetime(2026, 12, 24, 9, 30, tzinfo=worker.HONG_KONG),
            datetime(2026, 12, 24, 9, 30, 15, tzinfo=worker.HONG_KONG),  # After capture
            datetime(2026, 12, 25, 9, 30, tzinfo=worker.HONG_KONG),
            datetime(2026, 12, 26, 9, 30, tzinfo=worker.HONG_KONG),
            datetime(2026, 12, 28, 9, 30, 31, tzinfo=worker.HONG_KONG),
        ]
        with patch.dict("sys.modules", {"holidays": fake_calendar}), patch.dict(
            "os.environ", {
                "SCHEDULED_INSPECTION_ENABLED": "true",
                "SCHEDULED_INSPECTION_DAHAU_PASSWORD": "test-only",
            }, clear=True,
        ), patch("app.scheduled_inspection.connect") as connect, patch(
            "app.scheduled_inspection.DatabaseSettings",
        ), patch("app.scheduled_inspection._run") as run, patch(
            "app.scheduled_inspection._mark_missed_and_interrupted",
        ), patch("app.scheduled_inspection.clock.sleep"), patch(
            "app.scheduled_inspection.datetime",
        ) as worker_datetime:
            connect.return_value.cursor.return_value.execute.return_value.fetchone.return_value = (0,)
            worker_datetime.combine.side_effect = datetime.combine
            worker_datetime.now.side_effect = [*polls, KeyboardInterrupt]
            with self.assertRaises(KeyboardInterrupt):
                worker.main()

        calendar_factory.assert_called_once_with("HK", observed=True)
        run.assert_called_once_with(date(2026, 12, 24), worker.DEFAULT_SNAPSHOT_DIR, "test-only", False)

    def test_snapshot_uses_each_recorder_address_and_channel(self) -> None:
        with patch("app.recorder.build_opener") as build_opener:
            opener = build_opener.return_value
            opener.open.return_value.__enter__.return_value.read.return_value = b"snapshot"
            self.assertEqual(Recorder("192.0.2.1:8081", "admin", "test-only").snapshot(2), b"snapshot")
            self.assertEqual(Recorder("192.0.2.2", "admin", "test-only").snapshot(1), b"snapshot")
        self.assertEqual(opener.open.call_args_list, [
            call("http://192.0.2.1:8081/cgi-bin/snapshot.cgi?channel=2", timeout=10),
            call("http://192.0.2.2:81/cgi-bin/snapshot.cgi?channel=1", timeout=10),
        ])

    def test_enabled_worker_requires_shared_password_before_database_access(self) -> None:
        with patch.dict("os.environ", {"SCHEDULED_INSPECTION_ENABLED": "true"}, clear=True), patch(
            "app.scheduled_inspection.connect",
        ) as connect, patch("app.scheduled_inspection.RecorderSettings",
                           side_effect=lambda: RecorderSettings(_env_file=None)), self.assertLogs(
            "app.scheduled_inspection", level="ERROR",
        ) as logs:
            with self.assertRaises(SystemExit) as exit_context:
                worker.main()
        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn("SCHEDULED_INSPECTION_DAHAU_PASSWORD", logs.output[0])
        connect.assert_not_called()

    def test_shared_password_loads_from_env_file_with_process_override(self) -> None:
        with TemporaryDirectory() as folder, patch.dict("os.environ", {}, clear=True):
            env_file = Path(folder) / "test.env"
            env_file.write_text("SCHEDULED_INSPECTION_DAHAU_PASSWORD=dummy-file-password\n", encoding="utf-8")
            settings = RecorderSettings(_env_file=env_file)
            self.assertEqual(settings.dahau_password.get_secret_value(), "dummy-file-password")
            self.assertNotIn("dummy-file-password", repr(settings))
            with patch.dict("os.environ", {"SCHEDULED_INSPECTION_DAHAU_PASSWORD": "dummy-process-password"}):
                self.assertEqual(RecorderSettings(_env_file=env_file).dahau_password.get_secret_value(),
                                 "dummy-process-password")

    def test_batch_plans_all_channels_before_contacting_any_recorder(self) -> None:
        rows = [("001", "Hong Kong", "192.0.2.1", 2), ("002", "Hong Kong", "192.0.2.2", 3)]
        day = date(2026, 11, 26)
        with TemporaryDirectory() as folder, patch("app.scheduled_inspection._execute"), patch(
            "app.scheduled_inspection._eligible_recorders", return_value=rows,
        ), patch("app.scheduled_inspection._recorder_identifier", side_effect=[str(uuid4()), str(uuid4())]), patch(
            "app.scheduled_inspection._add_item", side_effect=lambda *args: uuid4(),
        ) as add_item, patch("app.scheduled_inspection._capture_channels") as capture:
            def verify_queue(*args):
                self.assertEqual(add_item.call_count, 5)
            capture.side_effect = verify_queue
            worker._run(day, Path(folder), "dummy-password", False)
        self.assertEqual(capture.call_count, 2)

    def test_capture_only_saves_valid_jpeg_and_continues_after_bad_image(self) -> None:
        image = BytesIO()
        Image.new("RGB", (4, 3)).save(image, format="PNG")
        run_date = date(2026, 11, 26)
        recorder_id = str(uuid4())
        with TemporaryDirectory() as folder, patch(
            "app.scheduled_inspection.Recorder", autospec=True,
        ) as recorder_type, patch(
            "app.scheduled_inspection._recorder_identifier", return_value=recorder_id,
        ), patch("app.scheduled_inspection._execute") as execute, patch(
            "app.scheduled_inspection.AnalysisQueue",
        ) as queue:
            recorder = recorder_type.return_value
            recorder.base = "http://192.0.2.1:81"
            recorder.snapshot.side_effect = [b"<html>not a camera image</html>", image.getvalue()]

            worker._process_recorder(
                run_date, 1, ("001", "Hong Kong", "192.0.2.1:81", 2),
                set(), Path(folder), "test-only", False,
            )

            archived = list(Path(folder).rglob("*.jpeg"))
            self.assertEqual(archived, [Path(folder) / "2026-11-26" / recorder_id / "channel-002.jpeg"])
            with Image.open(archived[0]) as saved:
                self.assertEqual((saved.format, saved.size), ("JPEG", (4, 3)))
            recorder_type.assert_called_once_with("192.0.2.1:81", "admin", "test-only")
            self.assertEqual(recorder.snapshot.call_args_list, [call(1), call(2)])
            for item in execute.call_args_list[:2]:
                self.assertEqual(item.args[-2], "configured_count")
            self.assertIn("status='failed'", execute.call_args_list[-2].args[0])
            self.assertEqual(execute.call_args.args[1], "completed")
            queue.assert_not_called()

    def test_snapshot_authentication_failure_marks_remaining_channels_failed_without_retry(self) -> None:
        run_date = date(2026, 11, 26)
        with TemporaryDirectory() as folder, patch(
            "app.scheduled_inspection.Recorder", autospec=True,
        ) as recorder_type, patch(
            "app.scheduled_inspection._recorder_identifier", return_value=str(uuid4()),
        ), patch("app.scheduled_inspection._execute") as execute:
            recorder = recorder_type.return_value
            recorder.base = "http://192.0.2.1:81"
            recorder.snapshot.side_effect = RecorderAuthenticationError("Authentication failed.")

            worker._process_recorder(
                run_date, 1, ("001", "Hong Kong", "192.0.2.1:81", 2),
                set(), Path(folder), "test-only", False,
            )

            recorder.snapshot.assert_called_once_with(1)
            self.assertIn("status='failed'", execute.call_args.args[0])
            self.assertIn("Not attempted", execute.call_args.args[1])
            self.assertEqual(list(Path(folder).rglob("*.jpeg")), [])

    def test_missed_runs_only_record_working_days_after_start_time(self) -> None:
        public_holidays = {date(2026, 12, 25)}
        with patch("app.scheduled_inspection.connect") as connect, patch(
            "app.scheduled_inspection.DatabaseSettings",
        ), patch("app.scheduled_inspection._execute") as execute:
            cursor = connect.return_value.cursor.return_value
            cursor.execute.return_value.fetchone.return_value = (date(2026, 12, 24),)
            worker._mark_missed_and_interrupted(
                datetime(2026, 12, 29, 9, 29, 59, tzinfo=worker.HONG_KONG), public_holidays,
            )
            worker._mark_missed_and_interrupted(
                datetime(2026, 12, 28, 9, 30, tzinfo=worker.HONG_KONG), public_holidays,
            )
        self.assertEqual(
            [call.args[1] for call in execute.call_args_list if "'missed'" in call.args[0]],
            [date(2026, 12, 28), date(2026, 12, 28)],
        )
