"""Read-only Dahua and Hikvision snapshot adapters."""
import ipaddress
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, HTTPRedirectHandler,
    ProxyHandler, build_opener,
)

from app.inspection import MAX_UPLOAD_BYTES
from app.snapshot_storage import DEFAULT_SNAPSHOT_DIR
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_CHANNELS = 256


class RecorderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCHEDULED_INSPECTION_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    dahau_password: SecretStr = SecretStr("")
    hikvision_password: SecretStr = SecretStr("")
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR


class RecorderError(RuntimeError):
    pass


class RecorderAuthenticationError(RecorderError):
    pass


class RecorderChannelNotFoundError(RecorderError):
    pass


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def _address(value: str) -> str:
    value = value.strip()
    port = "81"
    if value.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\](?::([0-9]{1,5}))?", value)
        if match is None:
            raise RecorderError("Invalid internal IP address or port.")
        value, port = match[1], match[2] or port
    elif value.count(":") == 1:
        value, port = value.split(":")
    try:
        address = ipaddress.ip_address(value)
        if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
            raise ValueError
    except ValueError:
        raise RecorderError("Invalid internal IP address or port.") from None
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"{host}:{int(port)}"


class Recorder:
    def __init__(self, internal_ip: str, username: str, password: str, brand: str = "DAHAU"):
        if not username.strip() or not password.strip():
            raise RecorderError("Recorder credentials are incomplete.")
        self.base = f"http://{_address(internal_ip)}"
        self.brand = brand.strip().upper()
        manager = HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base, username.strip(), password)
        self.opener = build_opener(ProxyHandler({}), RejectRedirects(), HTTPDigestAuthHandler(manager))

    def _get(self, path: str, limit: int) -> bytes:
        try:
            with self.opener.open(self.base + path, timeout=10) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise RecorderError("Recorder response exceeded the size limit.")
                return body
        except HTTPError as error:
            if error.code == 401:
                raise RecorderAuthenticationError("Recorder authentication failed or digest is unsupported.") from None
            if error.code == 404:
                raise RecorderChannelNotFoundError("Recorder returned HTTP 404.") from None
            raise RecorderError(f"Recorder returned HTTP {error.code}.") from None
        except (URLError, OSError, ValueError):
            raise RecorderError("Recorder request failed or timed out.") from None

    def snapshot(self, channel: int) -> bytes:
        if not 1 <= channel <= MAX_CHANNELS:
            raise RecorderError("Invalid channel number.")
        if self.brand == "HIKVISION":
            for path in (
                f"/ISAPI/Streaming/channels/{channel * 100 + 1}/picture",
                f"/ISAPI/Streaming/channels/{channel}/picture",
            ):
                try:
                    return self._get(path, MAX_UPLOAD_BYTES)
                except RecorderChannelNotFoundError:
                    continue
            raise RecorderChannelNotFoundError("Recorder returned HTTP 404 for the configured channel.")
        return self._get(f"/cgi-bin/snapshot.cgi?channel={channel}", MAX_UPLOAD_BYTES)
