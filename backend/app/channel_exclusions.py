"""Persistent, recorder-specific channel exclusions shared by both capture modes."""
from contextlib import closing, contextmanager
import hashlib
from pathlib import Path
import sqlite3

from app.recorder import MAX_CHANNELS
from app.snapshot_storage import SnapshotStorageError


def recorder_key(shop: str, region: str, base: str) -> str:
    return hashlib.sha256(f"{region}\0{shop}\0{base}".encode("utf-8")).hexdigest()


class ChannelExclusions:
    def __init__(self, root: Path):
        self.path = root / "channel-exclusions.sqlite3"
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS channel_exclusions ("
                "recorder_key TEXT NOT NULL, channel INTEGER NOT NULL, reason TEXT NOT NULL, "
                "PRIMARY KEY (recorder_key, channel))"
            )

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
                yield connection
        except (OSError, sqlite3.Error):
            raise SnapshotStorageError("Channel exclusions could not be read or saved to the local archive.") from None

    def get(self, key: str) -> dict[int, str]:
        with self._connection() as connection:
            return dict(connection.execute(
                "SELECT channel, reason FROM channel_exclusions WHERE recorder_key=?", (key,),
            ))

    def set_skipped(self, key: str, channel: int, skip: bool) -> None:
        if not 1 <= channel <= MAX_CHANNELS:
            raise ValueError("Invalid channel number.")
        with self._connection() as connection:
            if skip:
                connection.execute(
                    "INSERT INTO channel_exclusions VALUES (?, ?, 'user') "
                    "ON CONFLICT(recorder_key, channel) DO UPDATE SET reason='user'", (key, channel),
                )
            else:
                connection.execute(
                    "DELETE FROM channel_exclusions WHERE recorder_key=? AND channel=?", (key, channel),
                )

    def record_results(self, key: str, present: set[int], not_found: set[int], *, recheck: bool = False) -> None:
        # A working snapshot endpoint is required before a 404 can identify a missing channel.
        if not present:
            return
        with self._connection() as connection:
            reason_filter = "" if recheck else " AND reason='not_found'"
            connection.executemany(
                f"DELETE FROM channel_exclusions WHERE recorder_key=? AND channel=?{reason_filter}",
                [(key, channel) for channel in present],
            )
            connection.executemany(
                "INSERT INTO channel_exclusions VALUES (?, ?, 'not_found') "
                "ON CONFLICT(recorder_key, channel) DO NOTHING",
                [(key, channel) for channel in not_found - present],
            )


def exclusion_message(reason: str) -> str:
    detail = "excluded by a user" if reason == "user" else "previously returned HTTP 404 on a working recorder"
    return f"Skipped: channel {detail}. Re-enable it or use Recheck skipped channels to retry."
