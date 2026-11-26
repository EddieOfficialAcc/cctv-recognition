"""Read-only DAHAU snapshot adapter. Device compatibility is not yet verified."""
from dataclasses import dataclass
import ipaddress
import re
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, HTTPRedirectHandler,
    ProxyHandler, build_opener,
)

from app.inspection import MAX_UPLOAD_BYTES

MAX_CHANNELS = 256
MAX_DISCOVERY_BYTES = 256 * 1024


class RecorderError(RuntimeError):
    pass


class RecorderAuthenticationError(RecorderError):
    pass


@dataclass(frozen=True)
class Channel:
    number: int
    name: str


@dataclass(frozen=True)
class Discovery:
    channels: list[Channel]
    method: str
    warning: str


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def _address(value: str) -> str:
    value = value.strip()
    if value.count(":") == 1 and value.endswith(":81"):
        value = value[:-3]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise RecorderError("Invalid internal IP address or port.") from None
    return f"[{address}]" if address.version == 6 else str(address)


class Recorder:
    def __init__(self, internal_ip: str, username: str, password: str):
        if not username.strip() or not password.strip():
            raise RecorderError("Recorder credentials are incomplete.")
        self.base = f"http://{_address(internal_ip)}:81"
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
            raise RecorderError(f"Recorder returned HTTP {error.code}.") from None
        except (URLError, OSError, ValueError):
            raise RecorderError("Recorder request failed or timed out.") from None

    def discover(self, quantity: int | None) -> Discovery:
        try:
            payload = self._get(
                "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle", MAX_DISCOVERY_BYTES,
            ).decode("utf-8")
            channels: dict[int, str] = {}
            for line in payload.splitlines():
                match = re.fullmatch(r"table\.ChannelTitle\[(\d+)\]\.Name=(.*)", line)
                if match:
                    number = int(match.group(1)) + 1
                    if not 1 <= number <= MAX_CHANNELS or number in channels:
                        raise RecorderError("Invalid or duplicate channel in recorder response.")
                    channels[number] = match.group(2).strip()[:100] or f"Channel {number}"
            if not channels:
                raise RecorderError("Recorder did not return recognizable channel titles.")
            return Discovery(
                [Channel(number, name) for number, name in sorted(channels.items())],
                "recorder_titles",
                "Channel titles may include inactive inputs or omit channels; coverage is unverified.",
            )
        except RecorderAuthenticationError:
            raise
        except (RecorderError, UnicodeDecodeError) as error:
            if quantity is None or not 1 <= quantity <= MAX_CHANNELS:
                raise RecorderError("Channel discovery failed and camera count is unavailable or invalid.") from None
            reason = str(error) if isinstance(error, RecorderError) else "Recorder discovery response is not UTF-8."
            return Discovery(
                [Channel(number, f"Channel {number}") for number in range(1, quantity + 1)],
                "count_fallback",
                f"{reason} Sequential fallback may miss channels that do not start at 1.",
            )

    def snapshot(self, channel: int) -> bytes:
        if not 1 <= channel <= MAX_CHANNELS:
            raise RecorderError("Invalid channel number.")
        return self._get(f"/cgi-bin/snapshot.cgi?channel={channel}", MAX_UPLOAD_BYTES)
