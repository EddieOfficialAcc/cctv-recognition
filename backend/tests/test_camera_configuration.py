import unittest
from unittest.mock import MagicMock, patch

import pyodbc
from fastapi.testclient import TestClient

from app.auth import require_user
from app.main import app


class CameraConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_authentication_is_required(self) -> None:
        with patch("app.auth.DatabaseSettings", side_effect=AssertionError("unexpected database access")):
            response = self.client.get("/api/camera-configuration")
        self.assertEqual(response.status_code, 401)

    def test_page_contains_only_allowed_fields_for_all_brands(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.return_value.fetchone.return_value = (2,)
        cursor.execute.return_value.fetchall.return_value = [
            ("1", "Shop 1", "Company", "Hong Kong", "DAHAU", "192.0.2.1:81", 81, 8, 1),
            ("2", "Shop 2", "Company", "Macau", "HIKVISION", None, None, None, 0),
        ]
        with patch("app.camera_configuration.DatabaseSettings"), patch(
            "app.camera_configuration.connect", return_value=connection,
        ):
            response = self.client.get("/api/camera-configuration?page=2&page_size=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual([row["brand"] for row in body["rows"]], ["DAHAU", "HIKVISION"])
        self.assertTrue(body["rows"][0]["credentials_complete"])
        self.assertFalse(body["rows"][1]["credentials_complete"])
        self.assertNotIn("admin_name", body["rows"][0])
        self.assertNotIn("admin_password", body["rows"][0])
        sql, offset, limit = cursor.execute.call_args.args
        self.assertNotIn("WHERE", sql.upper())
        self.assertEqual((offset, limit), (2, 2))
        connection.close.assert_called_once()

    def test_database_failure_does_not_expose_details(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        with patch("app.camera_configuration.DatabaseSettings"), patch(
            "app.camera_configuration.connect", side_effect=pyodbc.Error("private details"),
        ), self.assertLogs("app.camera_configuration", level="ERROR") as logs:
            response = self.client.get("/api/camera-configuration")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private details", response.text)
        self.assertNotIn("private details", "".join(logs.output))


if __name__ == "__main__":
    unittest.main()
