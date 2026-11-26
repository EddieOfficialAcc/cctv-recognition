from contextlib import closing, contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

SIGNAL_CRITERIA = """For signal, look for visible black/blank images, no-signal messages and other visible signal faults.
If frames are supplied, consider possible repeated frames and flicker but do not claim a live feed is healthy."""
SCENE_CRITERIA = """For scene, assess visible obstruction, and compare the camera viewpoint to the approved baseline if provided.
Without a baseline, do not claim the camera viewpoint is unchanged."""


class CheckingRuleDefaults(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISION_", env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore", hide_input_in_errors=True,
    )
    drift_seconds: int = Field(default=180, ge=1, le=86400)


class RuleValues(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    signal_instructions: str = Field(default=SIGNAL_CRITERIA, min_length=1, max_length=1500)
    scene_instructions: str = Field(default=SCENE_CRITERIA, min_length=1, max_length=1500)
    timestamp_position: Literal["upper-right", "upper-left", "lower-right", "lower-left"] = "upper-right"
    timestamp_drift_seconds: int = Field(default=180, strict=True, ge=1, le=86400)


class CheckingRules(RuleValues):
    revision: int = Field(default=0, strict=True, ge=0)
    updated_at: datetime | None = None


class RuleUpdate(RuleValues):
    revision: int = Field(strict=True, ge=0)


class RulesPage(BaseModel):
    rules: CheckingRules
    defaults: RuleValues


class RulesStorageError(RuntimeError):
    pass


class RulesConflict(RuntimeError):
    pass


class RulesStore:
    def __init__(self, root: Path, default_drift_seconds: int = 180):
        self.path = root / "checking-rules.sqlite3"
        self.defaults = RuleValues(timestamp_drift_seconds=default_drift_seconds)
        with self._connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS checking_rules ("
                "id INTEGER PRIMARY KEY CHECK(id=1), definition TEXT NOT NULL, "
                "revision INTEGER NOT NULL, updated_at TEXT NOT NULL)"
            )

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.row_factory = sqlite3.Row
                yield db
        except (OSError, sqlite3.Error, ValidationError) as error:
            logger.error("Checking rules storage failed (%s).", type(error).__name__)
            raise RulesStorageError("Checking rules storage unavailable.") from None

    def get(self) -> CheckingRules:
        with self._connection() as db:
            row = db.execute("SELECT definition, revision, updated_at FROM checking_rules WHERE id=1").fetchone()
            if row is None:
                return CheckingRules(**self.defaults.model_dump())
            values = RuleValues.model_validate_json(row["definition"])
            return CheckingRules(**values.model_dump(), revision=row["revision"], updated_at=row["updated_at"])

    def save(self, update: RuleUpdate) -> CheckingRules:
        values = RuleValues.model_validate(update.model_dump(exclude={"revision"}))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM checking_rules WHERE id=1").fetchone()
            revision = row["revision"] if row is not None else 0
            if revision != update.revision:
                raise RulesConflict("Checking rules changed. Reload before saving.")
            updated_at = datetime.now(timezone.utc)
            saved = CheckingRules(**values.model_dump(), revision=revision + 1, updated_at=updated_at)
            db.execute(
                "INSERT INTO checking_rules VALUES(1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET definition=excluded.definition, "
                "revision=excluded.revision, updated_at=excluded.updated_at",
                (values.model_dump_json(), saved.revision, updated_at.isoformat()),
            )
            return saved


def load_checking_rules(default_drift_seconds: int) -> CheckingRules:
    # Recorder configuration imports the inspection pipeline, so defer this import.
    from app.recorder import RecorderSettings

    return RulesStore(RecorderSettings().snapshot_dir, default_drift_seconds).get()
