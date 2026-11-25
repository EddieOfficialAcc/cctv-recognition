from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import pyodbc
from fastapi.testclient import TestClient

from app import auth
from app.main import app


class PasswordTest(unittest.TestCase):
    def test_password_hash_is_salted_and_verifies(self):
        first = auth.hash_password("correct horse battery staple")
        second = auth.hash_password("correct horse battery staple")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password("correct horse battery staple", first))
        self.assertFalse(auth.verify_password("wrong password", first))
        self.assertNotIn("correct horse", first)

    def test_user_validation_before_database_access(self):
        with patch("app.auth.connect") as connect:
            for username, password in [("bad user", "a" * 12), ("admin", "short")]:
                with self.subTest(username=username), self.assertRaises(ValueError):
                    auth.create_user(username, password)
            connect.assert_not_called()


class AuthenticationDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.settings = patch("app.auth.DatabaseSettings")
        self.database = patch("app.auth.connect", return_value=self.connection)
        self.settings.start()
        self.database.start()
        self.addCleanup(self.settings.stop)
        self.addCleanup(self.database.stop)

    def test_fifth_failure_locks_account_without_creating_session(self):
        self.cursor.execute.return_value.fetchone.return_value = SimpleNamespace(
            id="user-id", password_hash="hash", is_active=True, failed_attempts=4, locked_until=None,
        )
        with patch("app.auth.verify_password", return_value=False):
            self.assertIsNone(auth.authenticate("Admin", "incorrect"))
        update = self.cursor.execute.call_args_list[-1].args
        self.assertIn("locked_until", update[0])
        self.assertEqual(update[1], 0)
        self.assertGreater(update[2], datetime.now(timezone.utc).replace(tzinfo=None))
        self.connection.commit.assert_called_once()

    def test_locked_or_disabled_account_cannot_log_in(self):
        for active, locked_until in [(False, None), (True, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5))]:
            with self.subTest(active=active):
                self.cursor.reset_mock()
                self.cursor.execute.return_value.fetchone.return_value = SimpleNamespace(
                    id="user-id", password_hash="hash", is_active=active,
                    failed_attempts=0, locked_until=locked_until,
                )
                with patch("app.auth.verify_password", return_value=True):
                    self.assertIsNone(auth.authenticate("admin", "correct"))
                self.assertEqual(self.cursor.execute.call_count, 1)

    def test_success_stores_hash_not_bearer_token(self):
        self.cursor.execute.return_value.fetchone.return_value = SimpleNamespace(
            id="user-id", password_hash="hash", is_active=True, failed_attempts=2, locked_until=None,
        )
        now = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        for remember_me, seconds in [(False, 8 * 60 * 60), (True, 30 * 24 * 60 * 60)]:
            with self.subTest(remember_me=remember_me), \
                 patch("app.auth.verify_password", return_value=True), \
                 patch("app.auth.datetime") as clock:
                clock.now.return_value = now
                token = auth.authenticate("Admin", "correct", remember_me)
                self.assertIsNotNone(token)
                insert = self.cursor.execute.call_args_list[-1].args
                self.assertIn("INSERT INTO dbo.auth_sessions", insert[0])
                self.assertEqual(insert[1], auth.hashlib.sha256(token.encode("ascii")).digest())
                self.assertNotEqual(insert[1], token)
                self.assertEqual(insert[3], now.replace(tzinfo=None) + timedelta(seconds=seconds))
                self.assertEqual(insert[4], remember_me)


class AuthApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Requested-With": "XMLHttpRequest"}

    def test_recognition_and_evidence_require_session(self):
        self.assertEqual(self.client.get("/api/recognitions").status_code, 401)
        self.assertEqual(self.client.get("/api/recognitions/00000000-0000-0000-0000-000000000001/evidence").status_code, 401)

    def test_login_session_and_logout(self):
        with patch("app.auth.authenticate", return_value="secret-token"), \
             patch("app.auth.session_user", return_value="operator"), \
             patch("app.auth.delete_session") as delete:
            login = self.client.post("/api/auth/login", headers=self.headers, json={"username": "Operator", "password": "password"})
            self.assertEqual(login.status_code, 200)
            self.assertIn("HttpOnly", login.headers["set-cookie"])
            self.assertIn("SameSite=strict", login.headers["set-cookie"])
            self.assertIn("Secure", login.headers["set-cookie"])
            self.client.cookies.set(auth.COOKIE_NAME, "secret-token")
            self.assertEqual(self.client.get("/api/auth/session").json(), {"username": "operator"})
            self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
            self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers).status_code, 200)
            delete.assert_called_once_with("secret-token")

    def test_remember_me_controls_cookie_persistence_and_authentication(self):
        for choice in [None, False, True]:
            with self.subTest(remember_me=choice), \
                 patch("app.auth.authenticate", return_value="secret-token") as authenticate:
                body = {"username": "Operator", "password": "password"}
                if choice is not None:
                    body["remember_me"] = choice
                response = self.client.post("/api/auth/login", headers=self.headers, json=body)
                self.assertEqual(response.status_code, 200)
                authenticate.assert_called_once_with("Operator", "password", choice is True)
                cookie = response.headers["set-cookie"]
                self.assertIn("HttpOnly", cookie)
                self.assertIn("Secure", cookie)
                self.assertIn("SameSite=strict", cookie)
                self.assertEqual(response.headers["cache-control"], "no-store")
                if choice:
                    self.assertIn("Max-Age=2592000", cookie)
                else:
                    self.assertNotIn("Max-Age", cookie)
                    self.assertNotIn("expires=", cookie.lower())
                self.assertNotIn("password", cookie)

    def test_remember_me_requires_a_boolean(self):
        with patch("app.auth.authenticate") as authenticate:
            for value in ["true", "false", 1, None]:
                with self.subTest(value=value):
                    response = self.client.post("/api/auth/login", headers=self.headers, json={
                        "username": "operator", "password": "password", "remember_me": value,
                    })
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn("set-cookie", response.headers)
            authenticate.assert_not_called()

    def test_invalid_credentials_and_database_failure_are_explicit(self):
        with patch("app.auth.authenticate", return_value=None):
            response = self.client.post("/api/auth/login", headers=self.headers, json={"username": "user", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", response.headers)
        with patch("app.auth.authenticate", side_effect=pyodbc.Error("private details")):
            response = self.client.post("/api/auth/login", headers=self.headers, json={"username": "user", "password": "wrong"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private details", response.text)

    def test_cross_site_write_is_rejected_even_with_session(self):
        self.client.cookies.set(auth.COOKIE_NAME, "secret-token")
        with patch("app.auth.session_user", return_value="operator") as session_user:
            response = self.client.post("/api/recognitions", data={})
        self.assertEqual(response.status_code, 403)
        session_user.assert_not_called()

    def test_login_protects_evidence_and_logout_revokes_session(self):
        sessions = {}
        user = SimpleNamespace(
            id="user-id", password_hash=auth.hash_password("correct password"),
            username="operator", is_active=True, failed_attempts=0, locked_until=None,
        )
        connection = MagicMock()
        cursor = connection.cursor.return_value
        result = SimpleNamespace(row=None)

        def execute(query, *params):
            if query.startswith("SELECT id, password_hash"):
                result.row = user if params[0] == user.username else None
            elif query.startswith("INSERT INTO dbo.auth_sessions"):
                sessions[params[0]] = params[1]
            elif query.startswith("SELECT u.username"):
                result.row = user if sessions.get(params[0]) == user.id else None
            elif query.startswith("DELETE FROM dbo.auth_sessions WHERE token_hash"):
                sessions.pop(params[0], None)
            cursor.fetchone.return_value = result.row
            return cursor

        cursor.execute.side_effect = execute
        with patch("app.auth.DatabaseSettings"), patch("app.auth.connect", return_value=connection), \
             patch("app.auth.AuthSettings", return_value=SimpleNamespace(cookie_secure=False)), \
             patch("app.recognition.repository.get_evidence", return_value=b"image"):
            login = self.client.post(
                "/api/auth/login", headers=self.headers,
                json={"username": "Operator", "password": "correct password", "remember_me": True},
            )
            self.assertEqual(login.status_code, 200)
            self.assertIn("Max-Age=2592000", login.headers["set-cookie"])
            self.assertEqual(self.client.get("/api/auth/session").json(), {"username": "operator"})
            evidence_path = "/api/recognitions/00000000-0000-0000-0000-000000000001/evidence"
            self.assertEqual(self.client.get(evidence_path).content, b"image")
            self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers).status_code, 200)
            self.assertEqual(self.client.get(evidence_path).status_code, 401)
