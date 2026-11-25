import hashlib
import hmac
import logging
import re
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pyodbc
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.database import DatabaseSettings, connect

logger = logging.getLogger(__name__)
COOKIE_NAME = "cctv_session"
SESSION_SECONDS = 8 * 60 * 60
REMEMBER_ME_SECONDS = 30 * 24 * 60 * 60
USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{1,64}\Z")


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTH_", env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
    )
    cookie_secure: bool = True


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=32768, r=8, p=3,
        dklen=32, maxmem=128 * 1024 * 1024,
    )
    return f"scrypt$32768$8$3${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[:4] != ["scrypt", "32768", "8", "3"]:
        logger.error("Unsupported password hash format.")
        return False
    try:
        salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
    except ValueError:
        logger.error("Invalid password hash encoding.")
        return False
    if len(salt) != 16 or len(expected) != 32:
        logger.error("Invalid password hash length.")
        return False
    actual = bytes.fromhex(hash_password(password, salt).split("$")[-1])
    return hmac.compare_digest(actual, expected)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def create_user(username: str, password: str) -> None:
    username = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Username must contain 1-64 ASCII letters, numbers, dots, underscores or hyphens.")
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12-256 characters.")
    encoded = hash_password(password)
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                "INSERT INTO dbo.auth_users (id, username, password_hash) VALUES (?, ?, ?)",
                str(uuid4()), username, encoded,
            )


def authenticate(username: str, password: str, remember_me: bool = False) -> str | None:
    username = normalize_username(username)
    with closing(connect(DatabaseSettings())) as connection:
        connection.autocommit = False
        try:
            with closing(connection.cursor()) as cursor:
                row = cursor.execute(
                    "SELECT id, password_hash, is_active, failed_attempts, locked_until "
                    "FROM dbo.auth_users WITH (UPDLOCK, ROWLOCK) WHERE username = ?",
                    username,
                ).fetchone()
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if row is None:
                    verify_password(password, DUMMY_HASH)
                    connection.commit()
                    return None
                valid = verify_password(password, row.password_hash)
                if not row.is_active or (row.locked_until and row.locked_until > now):
                    connection.commit()
                    return None
                if not valid:
                    failures = row.failed_attempts + 1
                    locked_until = now + timedelta(minutes=15) if failures >= 5 else None
                    cursor.execute(
                        "UPDATE dbo.auth_users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                        0 if locked_until else failures, locked_until, row.id,
                    )
                    connection.commit()
                    return None
                token = secrets.token_urlsafe(32)
                cursor.execute(
                    "UPDATE dbo.auth_users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", row.id,
                )
                cursor.execute("DELETE FROM dbo.auth_sessions WHERE expires_at <= ?", now)
                cursor.execute(
                    "INSERT INTO dbo.auth_sessions (token_hash, user_id, expires_at, remember_me) VALUES (?, ?, ?, ?)",
                    hashlib.sha256(token.encode("ascii")).digest(), row.id,
                    now + timedelta(seconds=REMEMBER_ME_SECONDS if remember_me else SESSION_SECONDS),
                    remember_me,
                )
            connection.commit()
            return token
        except (pyodbc.Error, ValueError):
            connection.rollback()
            raise


def session_user(token: str) -> str | None:
    try:
        token_hash = hashlib.sha256(token.encode("ascii")).digest()
    except UnicodeEncodeError:
        return None
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "SELECT u.username FROM dbo.auth_sessions s "
                "JOIN dbo.auth_users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > SYSUTCDATETIME() AND u.is_active = 1",
                token_hash,
            ).fetchone()
            return str(row.username) if row else None


def delete_session(token: str) -> None:
    try:
        token_hash = hashlib.sha256(token.encode("ascii")).digest()
    except UnicodeEncodeError:
        return
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute("DELETE FROM dbo.auth_sessions WHERE token_hash = ?", token_hash)


def csrf_check(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise HTTPException(403, "Invalid request.")


def require_user(request: Request) -> str:
    csrf_check(request)
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        raise HTTPException(401, "Authentication required.")
    try:
        username = session_user(token)
    except (pyodbc.Error, ValidationError) as error:
        logger.error("Authentication database operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "Authentication storage unavailable.") from None
    if not username:
        raise HTTPException(401, "Authentication required.")
    return username


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = Field(default=False, strict=True)


router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    csrf_check(request)
    try:
        settings = AuthSettings()
        token = authenticate(body.username, body.password, body.remember_me)
    except (pyodbc.Error, ValidationError) as error:
        logger.error("Login failed due to configuration or storage (%s).", type(error).__name__)
        raise HTTPException(503, "Authentication storage unavailable.") from None
    if not token:
        raise HTTPException(401, "Invalid credentials or account unavailable.")
    response.set_cookie(
        COOKIE_NAME, token, max_age=REMEMBER_ME_SECONDS if body.remember_me else None,
        httponly=True, secure=settings.cookie_secure,
        samesite="strict", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"username": normalize_username(body.username)}


@router.get("/session")
def session(response: Response, username: str = Depends(require_user)) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"username": username}


@router.post("/logout")
def logout(request: Request, response: Response, _: str = Depends(require_user)) -> dict[str, str]:
    try:
        delete_session(request.cookies[COOKIE_NAME])
    except (pyodbc.Error, ValidationError) as error:
        logger.error("Logout failed due to storage (%s).", type(error).__name__)
        raise HTTPException(503, "Authentication storage unavailable.") from None
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


DUMMY_HASH = hash_password("not-a-real-password", b"\x00" * 16)
