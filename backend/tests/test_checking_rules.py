import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import require_user
from app.checking_rules import LEGACY_SCENE_CRITERIA, SCENE_CRITERIA, RuleUpdate, RulesConflict, RulesStorageError, RulesStore
from app.main import app


@patch.dict(os.environ, {"SNAPSHOT_ANALYSIS_WORKER_ENABLED": "false", "VISION_DRIFT_SECONDS": "300"})
class CheckingRulesTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        environment = patch.dict(os.environ, {"SCHEDULED_INSPECTION_SNAPSHOT_DIR": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def authenticate(self):
        app.dependency_overrides[require_user] = lambda: "test-user"
        self.addCleanup(app.dependency_overrides.pop, require_user)

    def test_api_persists_rules_and_rejects_stale_or_invalid_changes(self):
        self.authenticate()
        with TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}) as client:
            response = client.get("/api/checking-rules")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            page = response.json()
            self.assertEqual(page["rules"]["revision"], 0)
            self.assertEqual(page["defaults"]["timestamp_drift_seconds"], 300)
            update = {**page["defaults"], "revision": 0, "timestamp_drift_seconds": 600,
                      "timestamp_position": "lower-left", "signal_instructions": "Look for a blue no-signal overlay."}
            response = client.put("/api/checking-rules", json=update)
            self.assertEqual(response.status_code, 200)
            saved = response.json()["rules"]
            self.assertEqual(saved["revision"], 1)
            self.assertIsNotNone(saved["updated_at"])
            self.assertEqual(RulesStore(self.root).get().signal_instructions, update["signal_instructions"])
            self.assertEqual(client.get("/api/checking-rules").json()["rules"], saved)
            self.assertEqual(client.put("/api/checking-rules", json=update).status_code, 409)
            for invalid in (
                {"timestamp_drift_seconds": 0}, {"timestamp_drift_seconds": 86401},
                {"timestamp_drift_seconds": True}, {"timestamp_drift_seconds": 1.5},
                {"timestamp_position": "anywhere"}, {"signal_instructions": "   "},
                {"scene_instructions": "x" * 1501}, {"revision": -1}, {"unexpected": True},
            ):
                with self.subTest(invalid=invalid):
                    self.assertEqual(client.put("/api/checking-rules", json={
                        **update, "revision": 1, **invalid,
                    }).status_code, 422)
            self.assertEqual(client.get("/api/checking-rules").json()["rules"], saved)
            reset = client.put("/api/checking-rules", json={**page["defaults"], "revision": 1})
            self.assertEqual(reset.json()["rules"]["revision"], 2)
            self.assertEqual(reset.json()["rules"]["timestamp_position"], "upper-right")

    def test_authentication_and_csrf_are_required(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/checking-rules").status_code, 401)
            self.assertEqual(client.put("/api/checking-rules", json={}).status_code, 403)
            self.assertEqual(client.put("/api/checking-rules", json={},
                                       headers={"X-Requested-With": "XMLHttpRequest"}).status_code, 401)

    def test_corrupt_storage_is_not_replaced_with_defaults(self):
        store = RulesStore(self.root)
        store.save(RuleUpdate(**store.defaults.model_dump(), revision=0))
        with store._connection() as db:
            db.execute("UPDATE checking_rules SET definition='invalid'")
        with self.assertRaises(RulesStorageError):
            RulesStore(self.root).get()
        self.authenticate()
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/checking-rules").status_code, 503)

    def test_independent_stores_cannot_overwrite_a_newer_revision(self):
        first, second = RulesStore(self.root), RulesStore(self.root)
        first.save(RuleUpdate(**first.defaults.model_dump(), revision=0))
        with self.assertRaises(RulesConflict):
            second.save(RuleUpdate(**second.defaults.model_dump(), revision=0))
        self.assertEqual(second.get().revision, 1)

    def test_legacy_default_scene_is_updated_without_overwriting_custom_rules(self):
        store = RulesStore(self.root)
        store.save(RuleUpdate(**{**store.defaults.model_dump(), "scene_instructions": LEGACY_SCENE_CRITERIA},
                              revision=0))
        self.assertEqual(store.get().scene_instructions, SCENE_CRITERIA)
        store.save(RuleUpdate(**{**store.defaults.model_dump(), "scene_instructions": "Custom scene rule."},
                              revision=1))
        self.assertEqual(store.get().scene_instructions, "Custom scene rule.")
