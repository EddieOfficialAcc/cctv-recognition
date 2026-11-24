from datetime import datetime, timezone
from io import BytesIO
import os
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pyodbc
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.inspection import InvalidScreenshot, MAX_UPLOAD_BYTES, analyze_burst, analyze_screenshot
from app.main import app
from app import recognition_repository as repository
from app.recognition_models import Baseline, RecognitionRecord


def screenshot(color="black", textured=False, format="PNG") -> bytes:
    image = Image.new("RGB", (160, 90), color)
    if textured:
        draw = ImageDraw.Draw(image)
        for x in range(0, 160, 16):
            draw.rectangle((x, 10, x + 8, 80), fill="white")
    buffer = BytesIO()
    image.save(buffer, format=format)
    return buffer.getvalue()


def make_record() -> RecognitionRecord:
    analysis = analyze_screenshot(screenshot())
    return RecognitionRecord(
        id=uuid4(), shop_code="TEST-001", camera_name="CH01", region="Hong Kong",
        filename="test.png", uploaded_at=datetime.now(timezone.utc), captured_at=None,
        width=160, height=90, outcome="flagged", review_status="new", review_note="",
        reviewed_at=None, analyzer_version="screenshot-statistics-v1", checks=analysis.checks,
    )


class ScreenshotTest(unittest.TestCase):
    def test_black_and_solid_feed_flagged_but_textured_scene_is_not(self) -> None:
        for color, textured, status in [
            ("black", False, "flagged"), ("blue", False, "flagged"),
            ("gray", True, "pass"), ("black", True, "pass"),
        ]:
            with self.subTest(color=color, textured=textured):
                analysis = analyze_screenshot(screenshot(color, textured))
                self.assertEqual(analysis.checks[0].status, status)
                self.assertEqual([c.status for c in analysis.checks[1:]], ["not_assessed"] * 2)
                with Image.open(BytesIO(analysis.evidence)) as evidence:
                    self.assertEqual(evidence.format, "JPEG")
                    self.assertEqual(evidence.size, (160, 90))

    def test_invalid_oversized_and_unsupported_images_are_rejected(self) -> None:
        for content in (b"", b"not an image", b"x" * (MAX_UPLOAD_BYTES + 1), screenshot(format="GIF")):
            with self.subTest(size=len(content)):
                with self.assertRaises(InvalidScreenshot):
                    analyze_screenshot(content)
        with patch("app.inspection.MAX_IMAGE_PIXELS", 100):
            with self.assertRaisesRegex(InvalidScreenshot, "20 megapixels"):
                analyze_screenshot(screenshot())

    def test_animated_images_rejected(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "red").save(
            buffer, format="PNG", save_all=True,
            append_images=[Image.new("RGB", (8, 8), "blue")],
        )
        with self.assertRaisesRegex(InvalidScreenshot, "animated"):
            analyze_screenshot(buffer.getvalue())

    def test_burst_flags_repeated_frames_and_alternating_brightness(self) -> None:
        repeated = screenshot("gray", True)
        analysis, evidence = analyze_burst(repeated, [repeated] * 5)
        self.assertEqual(analysis.checks[0].status, "flagged")
        self.assertIn("frozen", analysis.checks[0].explanation)
        self.assertEqual(len(evidence), 5)
        for item in evidence:
            with Image.open(BytesIO(item)) as image:
                self.assertEqual(image.format, "JPEG")

        def striped(low: int, high: int) -> bytes:
            image = Image.new("L", (160, 90), low)
            draw = ImageDraw.Draw(image)
            for x in range(0, 160, 16):
                draw.rectangle((x, 0, x + 7, 89), fill=high)
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()

        dark, bright = striped(40, 80), striped(180, 220)
        analysis, _ = analyze_burst(dark, [bright, dark, bright, dark, bright])
        self.assertEqual(analysis.checks[0].status, "flagged")
        self.assertIn("flicker", analysis.checks[0].explanation)

    def test_burst_rejects_bad_count_size_and_mismatched_frames(self) -> None:
        first = screenshot("gray", True)
        other_size = BytesIO()
        Image.new("RGB", (20, 20)).save(other_size, format="PNG")
        for frames in (
            [first] * 4, [first] * 10, [first] * 4 + [b"x" * (1024 * 1024 + 1)],
            [first] * 4 + [other_size.getvalue()],
        ):
            with self.subTest(count=len(frames)), self.assertRaises(InvalidScreenshot):
                analyze_burst(first, frames)

    def test_burst_preserves_blank_alert_and_does_not_certify_motion(self) -> None:
        blank = screenshot()
        analysis, _ = analyze_burst(blank, [blank] * 5)
        self.assertEqual(analysis.checks[0].status, "flagged")
        self.assertIn("black or blank", analysis.checks[0].explanation)

        frames = []
        for offset in range(6):
            image = Image.new("RGB", (160, 90), "gray")
            ImageDraw.Draw(image).rectangle((25 + offset * 12, 10, 40 + offset * 12, 80), fill="white")
            output = BytesIO()
            image.save(output, format="PNG")
            frames.append(output.getvalue())
        analysis, _ = analyze_burst(frames[0], frames[1:])
        self.assertEqual(analysis.checks[0].status, "pass")
        self.assertIn("not ruled out", analysis.checks[0].explanation)


class RecognitionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, content=None, **fields):
        data = {"shop_code": " TEST-001 ", "camera_name": " CH01 ", "region": "Hong Kong"}
        data.update(fields)
        return self.client.post(
            "/api/recognitions", data=data,
            files={"screenshot": ("C:\\captures\\test.png", screenshot() if content is None else content, "image/png")},
        )

    def test_upload_persists_evidence_and_check_results_before_success(self) -> None:
        with patch("app.recognition.repository.save_record") as save:
            response = self.upload(captured_at="2026-11-24T10:30:00+08:00")
        self.assertEqual(response.status_code, 201)
        record, evidence = save.call_args.args
        self.assertEqual((record.shop_code, record.camera_name, record.filename), ("TEST-001", "CH01", "test.png"))
        self.assertEqual(record.outcome, "flagged")
        self.assertEqual(record.captured_at.utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(len(response.json()["checks"]), 3)
        with patch("app.recognition.repository.get_record", return_value=record):
            detail = self.client.get(f"/api/recognitions/{record.id}")
        self.assertEqual(detail.json(), response.json())
        with patch("app.recognition.repository.get_evidence", return_value=evidence):
            image = self.client.get(f"/api/recognitions/{record.id}/evidence")
        self.assertEqual(image.content, evidence)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertEqual(image.headers["cache-control"], "no-store")

    def test_normal_screenshot_is_never_reported_as_completely_healthy(self) -> None:
        with patch("app.recognition.repository.save_record"):
            response = self.upload(screenshot("gray", True))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["outcome"], "needs_review")
        self.assertIsNone(response.json()["captured_at"])

    @patch.dict(os.environ, {"OCR_ENABLED": "true", "OCR_DRIFT_SECONDS": "180"})
    def test_upload_uses_capture_time_for_timestamp_ocr(self) -> None:
        with patch("app.recognition.repository.save_record") as save:
            with patch("app.timestamp_ocr._read_text", return_value="2026-11-24 10:35:00"):
                response = self.upload(screenshot("gray", True), captured_at="2026-11-24T10:30:00+08:00")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["analyzer_version"], "screenshot-ocr-v2")
        self.assertEqual(response.json()["checks"][1]["status"], "flagged")
        self.assertEqual(response.json()["outcome"], "flagged")
        self.assertEqual(save.call_args.args[0].checks[1].status, "flagged")

    def test_invalid_upload_and_naive_capture_time_never_reach_storage(self) -> None:
        with patch("app.recognition.repository.save_record") as save:
            for fields in (
                {"content": b"corrupt"}, {"shop_code": " "}, {"camera_name": " "},
                {"region": "unknown"}, {"captured_at": "2026-11-24T10:30:00"},
            ):
                with self.subTest(fields=fields):
                    self.assertEqual(self.upload(**fields).status_code, 422)
            save.assert_not_called()

    def test_burst_upload_saves_frame_evidence_and_rejects_missing_duration(self) -> None:
        image = screenshot("gray", True)
        files = [("screenshot", ("first.png", image, "image/png"))] + [
            ("burst_frames", (f"{i}.png", image, "image/png")) for i in range(5)
        ]
        data = {"shop_code": "TEST", "camera_name": "CH01", "region": "Hong Kong"}
        with patch("app.recognition.repository.save_record") as save:
            invalid = self.client.post("/api/recognitions", data=data, files=files)
            self.assertEqual(invalid.status_code, 422)
            save.assert_not_called()
            response = self.client.post(
                "/api/recognitions", data={**data, "burst_duration_seconds": "2"}, files=files,
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["analyzer_version"], "burst-statistics-v1")
        self.assertEqual(response.json()["outcome"], "flagged")
        record, primary, extra, duration = save.call_args.args
        self.assertEqual((len(extra), duration), (5, 2.0))
        with patch("app.recognition.repository.get_burst_info", return_value=(6, 2.0)):
            self.assertEqual(self.client.get(f"/api/recognitions/{record.id}/burst").json(),
                             {"frame_count": 6, "duration_seconds": 2.0})
        with patch("app.recognition.repository.get_burst_frame", return_value=extra[0]):
            response = self.client.get(f"/api/recognitions/{record.id}/burst/1")
        self.assertEqual(response.content, extra[0])
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch("app.recognition.repository.get_burst_frame", return_value=None):
            self.assertEqual(self.client.get(f"/api/recognitions/{record.id}/burst/1").status_code, 404)
            self.assertEqual(self.client.get(f"/api/recognitions/{record.id}/burst/0").status_code, 422)

    def test_storage_failure_is_not_success_and_does_not_leak_details(self) -> None:
        with patch("app.recognition.repository.save_record", side_effect=pyodbc.Error("private server")):
            with self.assertLogs("app.recognition", level="ERROR") as logs:
                response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private server", response.text + "".join(logs.output))

    def test_list_validates_pagination_and_filters(self) -> None:
        with patch("app.recognition.repository.list_records") as listing:
            for query in ("page=0", "page_size=101", "review_status=bad", "outcome=healthy", "region=bad"):
                with self.subTest(query=query):
                    self.assertEqual(self.client.get(f"/api/recognitions?{query}").status_code, 422)
            listing.assert_not_called()

    def test_missing_records_and_invalid_review_are_explicit(self) -> None:
        record_id = uuid4()
        with patch("app.recognition.repository.get_record", return_value=None):
            self.assertEqual(self.client.get(f"/api/recognitions/{record_id}").status_code, 404)
        with patch("app.recognition.repository.get_evidence", return_value=None):
            self.assertEqual(self.client.get(f"/api/recognitions/{record_id}/evidence").status_code, 404)
        with patch("app.recognition.repository.update_review", return_value=None) as update:
            self.assertEqual(self.client.patch(
                f"/api/recognitions/{record_id}/review", json={"status": "acknowledged", "note": "checked"},
            ).status_code, 404)
            self.assertEqual(update.call_args.args[1].note, "checked")
            for body in ({"status": "healthy"}, {"status": "new", "note": "x" * 1001}):
                self.assertEqual(self.client.patch(f"/api/recognitions/{record_id}/review", json=body).status_code, 422)

    def test_baseline_approval_and_lookup_use_saved_record_evidence(self) -> None:
        record_id = uuid4()
        baseline = Baseline(record_id=record_id, approved_at=datetime.now(timezone.utc))
        with patch("app.recognition.repository.approve_baseline", return_value=baseline) as approve:
            response = self.client.post(f"/api/recognitions/{record_id}/baseline")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["record_id"], str(record_id))
        approve.assert_called_once_with(record_id)
        with patch("app.recognition.repository.get_baseline", return_value=baseline):
            self.assertEqual(self.client.get(f"/api/recognitions/{record_id}/baseline").json(), response.json())
        with patch("app.recognition.repository.get_baseline", return_value=None):
            self.assertEqual(self.client.get(f"/api/recognitions/{record_id}/baseline").status_code, 404)
        with patch("app.recognition.repository.approve_baseline", return_value=None):
            self.assertEqual(self.client.post(f"/api/recognitions/{record_id}/baseline").status_code, 404)

    def test_review_returns_saved_operator_decision_without_changing_checks(self) -> None:
        record = make_record()
        updated = record.model_copy(update={
            "review_status": "false_positive", "review_note": "Unlit store, feed working.",
            "reviewed_at": datetime.now(timezone.utc),
        })
        with patch("app.recognition.repository.update_review", return_value=updated):
            response = self.client.patch(
                f"/api/recognitions/{record.id}/review",
                json={"status": "false_positive", "note": "Unlit store, feed working."},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review_status"], "false_positive")
        self.assertEqual(response.json()["checks"], [c.model_dump() for c in record.checks])
        self.assertEqual(response.json()["outcome"], "flagged")


class RecognitionRepositoryTest(unittest.TestCase):
    def test_baseline_approval_uses_record_metadata_and_lookup_is_camera_scoped(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        record_id = uuid4()
        approved_at = datetime(2026, 11, 24, 2, 30)
        cursor.execute.return_value = cursor
        cursor.fetchone.return_value = (str(record_id), approved_at)
        with patch("app.recognition_repository.DatabaseSettings"):
            with patch("app.recognition_repository.connect", return_value=connection):
                approved = repository.approve_baseline(record_id)
                found = repository.get_baseline(record_id)
        self.assertEqual(approved, found)
        self.assertEqual(found.approved_at.tzinfo, timezone.utc)
        insert = cursor.execute.call_args_list[0]
        self.assertIn("SELECT id, region, shop_code, camera_name", insert.args[0])
        self.assertEqual(insert.args[2], str(record_id))
        query = cursor.execute.call_args_list[1]
        for field in ("region", "shop_code", "camera_name"):
            self.assertIn(f"b.{field}", query.args[0])
        self.assertEqual(query.args[1], str(record_id))

    def test_burst_frames_are_saved_with_the_record_in_one_transaction(self) -> None:
        connection = MagicMock()
        with patch("app.recognition_repository.DatabaseSettings"):
            with patch("app.recognition_repository.connect", return_value=connection):
                repository.save_record(make_record(), b"first", [b"second"] * 5, 2.0)
        self.assertEqual(connection.cursor.return_value.execute.call_count, 9)
        connection.commit.assert_called_once()

    def test_burst_frame_storage_failure_rolls_back_record_and_checks(self) -> None:
        connection = MagicMock()
        connection.cursor.return_value.execute.side_effect = [
            None, None, None, None, pyodbc.Error("frame insert failed"),
        ]
        with patch("app.recognition_repository.DatabaseSettings"):
            with patch("app.recognition_repository.connect", return_value=connection):
                with self.assertRaises(pyodbc.Error):
                    repository.save_record(make_record(), b"first", [b"second"] * 5, 2.0)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_save_is_atomic_and_rolls_back_partial_checks(self) -> None:
        for fail in (False, True):
            with self.subTest(fail=fail):
                connection = MagicMock()
                cursor = connection.cursor.return_value
                if fail:
                    cursor.execute.side_effect = [None, None, pyodbc.Error("check failed")]
                record = make_record()
                with patch("app.recognition_repository.DatabaseSettings"):
                    with patch("app.recognition_repository.connect", return_value=connection):
                        if fail:
                            with self.assertRaises(pyodbc.Error):
                                repository.save_record(record, b"evidence")
                            connection.commit.assert_not_called()
                            connection.rollback.assert_called_once()
                        else:
                            repository.save_record(record, b"evidence")
                            self.assertEqual(cursor.execute.call_count, 4)
                            self.assertEqual(len(cursor.execute.call_args_list[0].args), 16)
                            connection.commit.assert_called_once()
                self.assertFalse(connection.autocommit)
                cursor.close.assert_called_once()
                connection.close.assert_called_once()

    def test_list_binds_search_filters_and_returns_checks_with_utc_times(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        record = make_record()
        values = [getattr(record, name) for name in repository.FIELD_NAMES]
        values[0] = str(record.id)
        values[5] = record.uploaded_at.replace(tzinfo=None)
        cursor.execute.return_value = cursor
        cursor.fetchone.side_effect = [(1, 1, 0, 1), (1,)]
        cursor.fetchall.side_effect = [
            [tuple(values)], [(str(record.id), c.category, c.status, c.explanation) for c in record.checks],
        ]
        search = "'; DROP TABLE recognition_records; --"
        with patch("app.recognition_repository.DatabaseSettings"):
            with patch("app.recognition_repository.connect", return_value=connection):
                result = repository.list_records(2, 20, search, "flagged", "new", "Hong Kong")
        self.assertEqual(result.records[0], record)
        query = cursor.execute.call_args_list[2]
        self.assertNotIn(search, query.args[0])
        self.assertEqual(query.args[1:], (search, search, search, "flagged", "new", "Hong Kong", 20, 20))

    def test_review_changes_only_operator_fields(self) -> None:
        from app.recognition_models import ReviewUpdate

        connection = MagicMock()
        record = make_record()
        with patch("app.recognition_repository.DatabaseSettings"):
            with patch("app.recognition_repository.connect", return_value=connection):
                with patch("app.recognition_repository.get_record", return_value=record):
                    repository.update_review(record.id, ReviewUpdate(status="acknowledged", note=" verified "))
        query = connection.cursor.return_value.execute.call_args
        self.assertNotIn("outcome =", query.args[0])
        self.assertNotIn("recognition_checks", query.args[0])
        self.assertEqual(query.args[1:3], ("acknowledged", "verified"))
        self.assertIsInstance(query.args[3], datetime)
