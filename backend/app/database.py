import logging
from contextlib import closing
from pathlib import Path

import pyodbc
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DB_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    host: str = Field(min_length=1)
    port: int = Field(default=1433, ge=1, le=65535)
    name: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)
    driver: str = "ODBC Driver 18 for SQL Server"
    trust_server_certificate: bool = False


class DatabaseCheckError(RuntimeError):
    pass


def _odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def connect(settings: DatabaseSettings) -> pyodbc.Connection:
    connection_string = (
        f"DRIVER={_odbc_value(settings.driver)};"
        f"SERVER={_odbc_value(f'{settings.host},{settings.port}')};"
        f"DATABASE={_odbc_value(settings.name)};"
        f"UID={_odbc_value(settings.user)};"
        f"PWD={_odbc_value(settings.password.get_secret_value())};"
        "Encrypt=yes;"
        f"TrustServerCertificate={'yes' if settings.trust_server_certificate else 'no'};"
    )
    connection = pyodbc.connect(connection_string, timeout=5, autocommit=True)
    connection.timeout = 5
    return connection


def check_database() -> str:
    settings = DatabaseSettings()
    with closing(connect(settings)) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute("SELECT DB_NAME()").fetchone()
            if row is None or row[0] != settings.name:
                raise DatabaseCheckError("Connected database does not match DB_NAME.")
            return str(row[0])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        database = check_database()
    except (ValidationError, pyodbc.Error, DatabaseCheckError) as error:
        logger.error("Database check failed (%s).", type(error).__name__)
        raise SystemExit(
            "Check backend/.env, the ODBC driver, network access and database permissions."
        ) from None
    print(f"Database connected: {database}")
