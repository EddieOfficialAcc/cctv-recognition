import os
import unittest
from unittest.mock import MagicMock, patch

import pyodbc
from pydantic import ValidationError

from app.database import DatabaseCheckError, DatabaseSettings, check_database, connect


class DatabaseTest(unittest.TestCase):
    def settings(self, **overrides) -> DatabaseSettings:
        values = {
            "_env_file": None,
            "host": "localhost",
            "name": "test_database",
            "user": "test_user",
            "password": "test_password",
        }
        values.update(overrides)
        with patch.dict(os.environ, {}, clear=True):
            return DatabaseSettings(**values)

    @patch("app.database.pyodbc.connect")
    def test_connection_escapes_credentials_and_enables_encryption(self, connect_mock) -> None:
        settings = self.settings(password="p};Encrypt=no;{")
        connection = connect(settings)

        connect_mock.assert_called_once()
        connection_string = connect_mock.call_args.args[0]
        self.assertIn("PWD={p}};Encrypt=no;{};", connection_string)
        self.assertIn(";Encrypt=yes;TrustServerCertificate=no;", connection_string)
        self.assertEqual(
            connect_mock.call_args.kwargs, {"timeout": 5, "autocommit": True}
        )
        self.assertEqual(connection.timeout, 5)
        self.assertNotIn("test_password", repr(self.settings()))

    def test_invalid_configuration_is_rejected(self) -> None:
        for overrides in ({"password": ""}, {"port": 0}, {"port": 65536}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.settings(**overrides)

    def test_check_verifies_database_and_closes_resources(self) -> None:
        settings = self.settings()
        for row in (("test_database",), ("master",), None):
            with self.subTest(row=row):
                connection = MagicMock()
                cursor = connection.cursor.return_value
                cursor.execute.return_value.fetchone.return_value = row
                with patch("app.database.DatabaseSettings", return_value=settings):
                    with patch("app.database.connect", return_value=connection):
                        if row == ("test_database",):
                            self.assertEqual(check_database(), "test_database")
                        else:
                            with self.assertRaises(DatabaseCheckError):
                                check_database()
                cursor.execute.assert_called_once_with("SELECT DB_NAME()")
                cursor.close.assert_called_once_with()
                connection.close.assert_called_once_with()

    def test_query_failure_closes_resources(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.side_effect = pyodbc.Error("query failed")
        with patch("app.database.DatabaseSettings", return_value=self.settings()):
            with patch("app.database.connect", return_value=connection):
                with self.assertRaises(pyodbc.Error):
                    check_database()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()
