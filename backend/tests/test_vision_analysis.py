from datetime import datetime, timezone
from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.auth import require_user
from app.vision_analysis import AnalysisError, TimestampEvidence, VisionSettings, _request, _timestamp_check, analyze


def model_result():
    explanation = {"en": "Visible scene.", "zh_hant": "可見場景。", "zh_hans": "可见场景。"}
    return {
        "signal": {"status": "pass", "explanation": explanation},
        "scene": {"status": "pass", "explanation": explanation},
        "timestamp": {"state": "readable", "reading": "2026-11-24 10:30:00"},
    }


@patch.dict(os.environ, {
    "VISION_API_KEY": "test-key",
    "VISION_ENDPOINT": "https://example.test/openai/v1",
    "VISION_DEPLOYMENT": "test-deployment",
    "VISION_DRIFT_SECONDS": "180",
})
class VisionAnalysisTest(unittest.TestCase):
    def test_one_request_includes_ordered_images_and_three_checks(self):
        body = {"choices": [{"message": {"content": json.dumps(model_result())}}]}
        with patch("app.vision_analysis.HTTPSConnection") as factory:
            response = factory.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps(body).encode()
            captured = datetime.fromisoformat("2026-11-24T10:30:00+08:00")
            checks = analyze(b"current", [b"frame1", b"frame2"], b"baseline", captured, 2.0)
        connection = factory.return_value
        connection.request.assert_called_once()
        self.assertEqual(connection.request.call_args.args[:2], ("POST", "/openai/v1/chat/completions"))
        request = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(request["model"], "test-deployment")
        self.assertIn("upper-left corner of the current screenshot", request["messages"][0]["content"])
        content = request["messages"][1]["content"]
        urls = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
        self.assertEqual(urls, [
            "data:image/jpeg;base64,Y3VycmVudA==",
            "data:image/jpeg;base64,YmFzZWxpbmU=",
            "data:image/jpeg;base64,ZnJhbWUx",
            "data:image/jpeg;base64,ZnJhbWUy",
        ])
        self.assertEqual([check.category for check in checks], ["signal", "timestamp", "scene"])
        self.assertEqual([check.status for check in checks], ["pass", "pass", "pass"])
        connection.close.assert_called_once()

    def test_missing_baseline_and_capture_time_do_not_pass(self):
        body = {"choices": [{"message": {"content": json.dumps(model_result())}}]}
        with patch("app.vision_analysis.HTTPSConnection") as factory:
            response = factory.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps(body).encode()
            checks = analyze(b"image", [], None, None, None)
        self.assertEqual([check.status for check in checks], ["pass", "not_assessed", "not_assessed"])

    def test_upload_to_persisted_checks_uses_one_model_call(self):
        app.dependency_overrides[require_user] = lambda: "test-user"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        body = {"choices": [{"message": {"content": json.dumps(model_result())}}]}
        screenshot = BytesIO()
        Image.new("RGB", (20, 20), "gray").save(screenshot, format="PNG")
        with TestClient(app) as client:
            with patch("app.recognition.repository.get_baseline_evidence", return_value=b"baseline"):
                with patch("app.recognition.repository.save_record") as save:
                    with patch("app.vision_analysis.HTTPSConnection") as factory:
                        response = factory.return_value.getresponse.return_value
                        response.status = 200
                        response.read.return_value = json.dumps(body).encode()
                        result = client.post(
                            "/api/recognitions",
                            data={"region": "Hong Kong", "shop_code": "shop", "camera_name": "camera",
                                  "captured_at": "2026-11-24T10:30:00+08:00"},
                            files={"screenshot": ("camera.png", screenshot.getvalue(), "image/png")},
                        )
        self.assertEqual(result.status_code, 201)
        factory.return_value.request.assert_called_once()
        self.assertEqual([check["status"] for check in result.json()["checks"]], ["pass", "pass", "pass"])
        self.assertEqual(result.json()["outcome"], "needs_review")
        save.assert_called_once()

    def test_timestamp_drift_and_uncertainty(self):
        captured = datetime(2026, 11, 24, 2, 30, tzinfo=timezone.utc)
        for state, text, expected, reason, difference in [
            ("readable", "2026-11-24 10:30:00", "pass", "within", 0),
            ("readable", "2026-11-24 10:31:59", "pass", "within", 119),
            ("readable", "2026-11-24 10:32:00", "not_assessed", "boundary", 120),
            ("readable", "2026-11-24 10:34:00", "not_assessed", "boundary", 240),
            ("readable", "2026-11-24 10:34:01", "flagged", "outside", 241),
            ("readable", "2026-11-24 10:25:00", "flagged", "outside", -300),
            ("readable", "2026-11-24 10:30", "not_assessed", "invalid", None),
            ("readable", "2026-02-31 10:30:00", "not_assessed", "invalid", None),
            ("missing", None, "not_assessed", "missing", None),
            ("unreadable", None, "not_assessed", "unreadable", None),
            ("ambiguous", None, "not_assessed", "ambiguous", None),
        ]:
            with self.subTest(state=state, text=text):
                check = _timestamp_check(TimestampEvidence(state=state, reading=text), captured, 180)
                self.assertEqual(check.status, expected)
                details = json.loads(check.explanation)
                self.assertEqual((details["reason"], details["difference_seconds"]), (reason, difference))
                self.assertEqual(details["threshold_seconds"], 180)
                self.assertEqual(details["reading"], text)

    def test_missing_capture_time_and_custom_threshold_preserve_evidence(self):
        evidence = TimestampEvidence(state="readable", reading="2026-11-24 10:35:00")
        check = _timestamp_check(evidence, None, 600)
        details = json.loads(check.explanation)
        self.assertEqual(check.status, "not_assessed")
        self.assertEqual(details["reason"], "capture_missing")
        self.assertEqual(details["reading"], evidence.reading)
        self.assertEqual(details["threshold_seconds"], 600)
        captured = datetime(2026, 11, 24, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(_timestamp_check(evidence, captured, 600).status, "pass")

    def test_service_failure_and_invalid_results_are_explicit(self):
        for status, content in [
            (401, b"private provider response"),
            (200, b"not-json"),
            (200, json.dumps({"choices": [{"message": {"content": '{"signal":{}}'}}]}).encode()),
            (200, json.dumps({"choices": [{"message": {"content": json.dumps({
                **model_result(), "timestamp": {"state": "missing", "reading": "2026-11-24 10:30:00"},
            })}}]}).encode()),
        ]:
            with self.subTest(status=status, content=content):
                with patch("app.vision_analysis.HTTPSConnection") as factory:
                    response = factory.return_value.getresponse.return_value
                    response.status = status
                    response.read.return_value = content
                    with self.assertRaises(AnalysisError) as error:
                        _request(VisionSettings(), [])
                self.assertNotIn("private provider response", str(error.exception))
                factory.return_value.close.assert_called_once()

    def test_non_https_endpoint_rejected_without_connecting(self):
        settings = VisionSettings(endpoint="http://example.test", api_key="test-key")
        with patch("app.vision_analysis.HTTPSConnection") as factory:
            with self.assertRaises(AnalysisError):
                _request(settings, [])
        factory.assert_not_called()

    def test_missing_key_and_timeout_fail_without_saving_a_result(self):
        with patch.dict(os.environ, {"VISION_API_KEY": ""}):
            with patch("app.vision_analysis.HTTPSConnection") as factory:
                with self.assertRaises(AnalysisError):
                    analyze(b"image", [], None, None, None)
            factory.assert_not_called()
        with patch("app.vision_analysis.HTTPSConnection") as factory:
            factory.return_value.request.side_effect = TimeoutError()
            with self.assertRaises(AnalysisError):
                analyze(b"image", [], None, None, None)
            factory.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
