"""Apply the additive SQL Server recognition schema: python -m app.schema."""
from contextlib import closing
import logging
from pathlib import Path

import pyodbc
from pydantic import ValidationError

from app.database import DatabaseSettings, connect


def apply_schema() -> None:
    schema_sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with closing(connect(DatabaseSettings())) as connection:
        connection.autocommit = False
        try:
            with closing(connection.cursor()) as cursor:
                cursor.execute(schema_sql)
            connection.commit()
        except pyodbc.Error:
            connection.rollback()
            raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        apply_schema()
    except (OSError, ValidationError, pyodbc.Error) as error:
        logging.error("Schema setup failed (%s).", type(error).__name__)
        raise SystemExit("Check the schema file, database connectivity and CREATE TABLE permissions.") from None
    print("Recognition tables are ready.")
