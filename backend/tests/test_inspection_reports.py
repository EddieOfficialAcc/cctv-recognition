from datetime import date
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.auth import require_user
from app.main import app


class InspectionReportsTest(unittest.TestCase):
    def test_progress_includes_saved_snapshots_and_all_planned_channels(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        connection = MagicMock()
        connection.cursor.return_value.execute.return_value.fetchall.return_value = [
            (date(2026, 11, 26), "running", None, None, 5, 2, 1, 0, 0, 2),
            (date(2026, 11, 25), "completed", None, None, 0, 0, 0, 0, 0, 0),
        ]
        with TestClient(app) as client, patch("app.inspection_reports.DatabaseSettings"), patch(
            "app.inspection_reports.connect", return_value=connection,
        ):
            response = client.get("/api/inspection-runs")
        self.assertEqual(response.status_code, 200)
        run, empty = response.json()
        self.assertEqual((run["total"], run["completed"], run["failed"], run["saved"]), (5, 2, 1, 2))
        self.assertEqual(empty["saved"], 0)

