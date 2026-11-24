from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import pyodbc

from app.schema import apply_schema


class SchemaTest(unittest.TestCase):
    def test_setup_executes_the_standalone_script_and_commits(self) -> None:
        connection = MagicMock()
        with patch("app.schema.DatabaseSettings"):
            with patch("app.schema.connect", return_value=connection):
                apply_schema()
        expected = (Path(__file__).resolve().parents[1] / "app" / "schema.sql").read_text(encoding="utf-8")
        connection.cursor.return_value.execute.assert_called_once_with(expected)
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        connection.cursor.return_value.close.assert_called_once()
        connection.close.assert_called_once()

    def test_script_failure_rolls_back_and_closes_resources(self) -> None:
        connection = MagicMock()
        connection.cursor.return_value.execute.side_effect = pyodbc.Error("test failure")
        with patch("app.schema.DatabaseSettings"):
            with patch("app.schema.connect", return_value=connection):
                with self.assertRaises(pyodbc.Error):
                    apply_schema()
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()
