from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
import pyodbc
from fastapi.testclient import TestClient

from app.auth import require_user
from app.main import app


class CameraConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        archive = TemporaryDirectory()
        self.addCleanup(archive.cleanup)
        self.archive = Path(archive.name)
        environment = patch.dict("os.environ", {"SCHEDULED_INSPECTION_SNAPSHOT_DIR": archive.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_authentication_is_required(self) -> None:
        with patch("app.auth.DatabaseSettings", side_effect=AssertionError("unexpected database access")):
            response = self.client.get("/api/camera-configuration")
        self.assertEqual(response.status_code, 401)

    def test_manual_snapshot_requires_csrf_header_and_session(self) -> None:
        payload = {"shop_code": "001", "region": "Hong Kong", "channel": 2}
        self.assertEqual(self.client.post("/api/camera-configuration/snapshot", json=payload).status_code, 403)
        response = self.client.post("/api/camera-configuration/snapshot", json=payload,
                                    headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 401)

    def test_snapshot_recorder_choices_do_not_expose_addresses_or_credentials(self) -> None:
        path = "/api/camera-configuration/snapshot-recorders"
        self.assertEqual(self.client.get(path).status_code, 401)
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.return_value.fetchall.return_value = [("001", "Hong Kong", "Shop 1", 2)]
        with patch("app.camera_configuration.DatabaseSettings"), patch(
            "app.camera_configuration.connect", return_value=connection,
        ):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), [{"shop": "001", "region": "Hong Kong", "name": "Shop 1", "channels": 2}])
        self.assertIn("HAVING COUNT(*) = 1", cursor.execute.call_args.args[0])

    def test_manual_snapshot_uses_only_the_configured_recorder_and_returns_jpeg(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.return_value.fetchall.return_value = [("192.0.2.1:81", 2)]
        image = BytesIO()
        Image.new("RGB", (4, 3)).save(image, format="PNG")
        payload = {"shop_code": "001", "region": "Hong Kong", "channel": 2, "internal_ip": "192.0.2.2"}
        with patch.dict("os.environ", {"SCHEDULED_INSPECTION_DAHAU_PASSWORD": "dummy-only"}), patch(
            "app.camera_configuration.DatabaseSettings",
        ), patch("app.camera_configuration.connect", return_value=connection), patch(
            "app.camera_configuration.Recorder",
        ) as recorder:
            recorder.return_value.snapshot.return_value = image.getvalue()
            response = self.client.post("/api/camera-configuration/snapshot", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with Image.open(BytesIO(response.content)) as saved:
            self.assertEqual((saved.format, saved.size), ("JPEG", (4, 3)))
        self.assertEqual(cursor.execute.call_args.args[1:], ("001", "Hong Kong"))
        recorder.assert_called_once_with("192.0.2.1:81", "admin", "dummy-only")
        recorder.return_value.snapshot.assert_called_once_with(2)
        files = list(self.archive.rglob("*.jpeg"))
        self.assertEqual(len(files), 1)
        self.assertIn("manual", files[0].parts)
        self.assertEqual(files[0].name, "channel-002.jpeg")
        self.assertEqual(files[0].read_bytes(), response.content)
        self.assertIn(".jpeg", response.headers["content-disposition"])

    def test_manual_snapshot_rejects_unconfigured_channel_without_contacting_recorder(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        connection = MagicMock()
        connection.cursor.return_value.execute.return_value.fetchall.return_value = [("192.0.2.1:81", 2)]
        with patch.dict("os.environ", {"SCHEDULED_INSPECTION_DAHAU_PASSWORD": "dummy-only"}), patch(
            "app.camera_configuration.DatabaseSettings",
        ), patch("app.camera_configuration.connect", return_value=connection), patch(
            "app.camera_configuration.Recorder",
        ) as recorder:
            response = self.client.post("/api/camera-configuration/snapshot",
                                        json={"shop_code": "001", "region": "Hong Kong", "channel": 3})
        self.assertEqual(response.status_code, 422)
        recorder.assert_not_called()

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

    def test_brand_filter_applies_to_the_count_and_paginated_rows(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        for brand in ("DAHAU", "HIKVISION"):
            with self.subTest(brand=brand):
                connection = MagicMock()
                cursor = connection.cursor.return_value
                cursor.execute.return_value.fetchone.return_value = (4,)
                cursor.execute.return_value.fetchall.return_value = [
                    ("003", "Shop 3", "Company", "Hong Kong", brand, None, None, 2, 0),
                    ("004", "Shop 4", "Company", "Macau", brand, None, None, 1, 0),
                ]
                with patch("app.camera_configuration.DatabaseSettings"), patch(
                    "app.camera_configuration.connect", return_value=connection,
                ):
                    response = self.client.get(f"/api/camera-configuration?brand={brand}&page=2&page_size=2")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 4)
                self.assertEqual([row["brand"] for row in response.json()["rows"]], [brand, brand])
                count, rows = cursor.execute.call_args_list
                self.assertIn("WHERE brand = ?", count.args[0])
                self.assertEqual(count.args[1:], (brand,))
                self.assertIn("WHERE brand = ? ORDER BY", rows.args[0])
                self.assertEqual(rows.args[1:], (brand, 2, 2))

    def test_company_and_region_filters_combine_with_brand_before_pagination(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.return_value.fetchone.return_value = (1,)
        cursor.execute.return_value.fetchall.return_value = [
            ("M07", "Shop", "BestMart360", "Macau", "DAHAU", None, None, 2, 0),
        ]
        with patch("app.camera_configuration.DatabaseSettings"), patch(
            "app.camera_configuration.connect", return_value=connection,
        ):
            response = self.client.get(
                "/api/camera-configuration?brand=DAHAU&region=Macau&company=BestMart360&page=2&page_size=2"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        count, rows = cursor.execute.call_args_list
        predicate = "WHERE brand = ? AND region = ? AND company = ?"
        self.assertIn(predicate, count.args[0])
        self.assertIn(predicate, rows.args[0])
        self.assertEqual(count.args[1:], ("DAHAU", "Macau", "BestMart360"))
        self.assertEqual(rows.args[1:], ("DAHAU", "Macau", "BestMart360", 2, 2))

    def test_invalid_new_filters_are_rejected(self) -> None:
        app.dependency_overrides[require_user] = lambda: "operator"
        self.addCleanup(app.dependency_overrides.pop, require_user, None)
        for query in ("region=invalid", "company=invalid"):
            with self.subTest(query=query), patch(
                "app.camera_configuration.connect", side_effect=AssertionError("unexpected database access")
            ):
                self.assertEqual(self.client.get(f"/api/camera-configuration?{query}").status_code, 422)


if __name__ == "__main__":
    unittest.main()
