import unittest
from unittest.mock import patch

import pyodbc

from fastapi.testclient import TestClient

from app.main import app


class HealthTest(unittest.TestCase):
    def test_health(self) -> None:
        with TestClient(app) as client:
            response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("app.main.check_database", return_value="cctv_recognition")
    def test_database_health(self, check_database) -> None:
        with TestClient(app) as client:
            response = client.get("/api/health/database")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"status": "ok", "database": "cctv_recognition"}
        )
        check_database.assert_called_once_with()

    @patch("app.main.check_database", side_effect=pyodbc.Error("private details"))
    def test_database_failure_is_unavailable_without_leaking_details(self, _) -> None:
        with self.assertLogs("app.main", level="ERROR") as logs:
            with TestClient(app) as client:
                response = client.get("/api/health/database")
                health_response = client.get("/api/health")

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private details", response.text)
        self.assertNotIn("private details", "".join(logs.output))
        self.assertEqual(health_response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
