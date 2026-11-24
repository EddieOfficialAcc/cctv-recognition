from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from PIL import Image

from app.timestamp_ocr import check_timestamp, parse_timestamp


class TimestampOCRTest(unittest.TestCase):
    def test_parser_accepts_only_one_unambiguous_valid_year_first_timestamp(self) -> None:
        self.assertEqual(
            parse_timestamp("2026-11-24 10:30:45"),
            datetime.fromisoformat("2026-11-24T10:30:45+08:00"),
        )
        for text in (
            "24/11/2026 10:30", "2026-02-30 10:30", "2026-11-24 10:30 PM",
            "2026-11-24 10:30:xx", "2026-11-24 10:30 UTC",
            "2026-11-24 10:30+01:00",
            "2026-11-24 10:30 2026-11-25 10:30", "not legible",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_timestamp(text))

    @patch.dict(os.environ, {"OCR_ENABLED": "true", "OCR_DRIFT_SECONDS": "180"})
    def test_compares_ocr_with_actual_capture_time_and_keeps_threshold_uncertain(self) -> None:
        image = Image.new("RGB", (160, 90))
        captured_at = datetime(2026, 11, 24, 2, 30, tzinfo=timezone.utc)
        for text, status in (
            ("2026-11-24 10:31:00", "pass"),
            ("2026-11-24 10:33:00", "not_assessed"),
            ("2026-11-24 10:35:00", "flagged"),
            ("unreadable", "not_assessed"),
        ):
            with self.subTest(text=text), patch("app.timestamp_ocr._read_text", return_value=text):
                check = check_timestamp(image, captured_at)
                self.assertEqual(check.status, status)
                self.assertEqual(check.category, "timestamp")

    @patch.dict(os.environ, {"OCR_ENABLED": "true"})
    def test_missing_capture_time_does_not_run_ocr(self) -> None:
        with patch("app.timestamp_ocr._read_text") as read:
            check = check_timestamp(Image.new("RGB", (10, 10)), None)
        self.assertEqual(check.status, "not_assessed")
        read.assert_not_called()

    @patch.dict(os.environ, {"OCR_ENABLED": "true"})
    def test_engine_failure_is_logged_and_not_assessed(self) -> None:
        with patch("app.timestamp_ocr._read_text", side_effect=RuntimeError("private details")):
            with self.assertLogs("app.timestamp_ocr", level="ERROR") as logs:
                check = check_timestamp(Image.new("RGB", (10, 10)), datetime.now(timezone.utc))
        self.assertEqual(check.status, "not_assessed")
        self.assertNotIn("private details", "".join(logs.output))


if __name__ == "__main__":
    unittest.main()
