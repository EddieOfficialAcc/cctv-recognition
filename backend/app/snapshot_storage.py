from datetime import date
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "camera-snapshots"


class SnapshotStorageError(RuntimeError):
    pass


def save_snapshot(root: Path, run_date: date, capture_id: str, channel: int, evidence: bytes) -> Path:
    directory = root / run_date.isoformat() / capture_id
    destination = directory / f"channel-{channel:03d}.jpeg"
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(evidence)
        temporary.replace(destination)
        return destination
    except OSError:
        raise SnapshotStorageError("Could not write snapshot to the local archive.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove a temporary snapshot file.")
