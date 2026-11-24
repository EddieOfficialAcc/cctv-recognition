from contextlib import closing
from datetime import datetime, timezone
from uuid import UUID

import pyodbc

from app.database import DatabaseSettings, connect
from app.recognition_models import (
    Baseline, RecognitionCheck, RecognitionPage, RecognitionRecord, RecognitionSummary, ReviewUpdate,
)

COLUMNS = (
    "id, shop_code, camera_name, region, filename, uploaded_at, captured_at, "
    "width, height, outcome, review_status, review_note, reviewed_at, analyzer_version"
)
FIELD_NAMES = [column.strip() for column in COLUMNS.split(",")]


def _records(cursor: pyodbc.Cursor, rows: list[pyodbc.Row]) -> list[RecognitionRecord]:
    if not rows:
        return []
    placeholders = ",".join("?" for _ in rows)
    check_rows = cursor.execute(
        f"SELECT record_id, category, status, explanation FROM dbo.recognition_checks "
        f"WHERE record_id IN ({placeholders})",
        *(str(row[0]) for row in rows),
    ).fetchall()
    checks: dict[str, list[RecognitionCheck]] = {}
    for row in check_rows:
        checks.setdefault(str(row[0]).lower(), []).append(RecognitionCheck(
            category=row[1], status=row[2], explanation=row[3],
        ))
    result = []
    for row in rows:
        values = dict(zip(FIELD_NAMES, row))
        for field in ("uploaded_at", "captured_at", "reviewed_at"):
            if values[field] is not None:
                values[field] = values[field].replace(tzinfo=timezone.utc)
        values["checks"] = sorted(
            checks.get(str(row[0]).lower(), []),
            key=lambda check: ("signal", "timestamp", "scene").index(check.category),
        )
        result.append(RecognitionRecord.model_validate(values))
    return result


def save_record(
    record: RecognitionRecord, evidence: bytes,
    burst_frames: list[bytes] | None = None, burst_duration_seconds: float | None = None,
) -> None:
    if burst_frames and burst_duration_seconds is None:
        raise ValueError("A burst duration is required when saving burst frames.")
    with closing(connect(DatabaseSettings())) as connection:
        connection.autocommit = False
        try:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    f"INSERT INTO dbo.recognition_records ({COLUMNS}, evidence) "
                    f"VALUES ({','.join('?' for _ in range(15))})",
                    str(record.id), record.shop_code, record.camera_name, record.region,
                    record.filename, record.uploaded_at.replace(tzinfo=None),
                    record.captured_at.astimezone(timezone.utc).replace(tzinfo=None)
                    if record.captured_at else None,
                    record.width, record.height, record.outcome, record.review_status,
                    record.review_note, record.reviewed_at, record.analyzer_version,
                    pyodbc.Binary(evidence),
                )
                for check in record.checks:
                    cursor.execute(
                        "INSERT INTO dbo.recognition_checks "
                        "(record_id, category, status, explanation) VALUES (?, ?, ?, ?)",
                        str(record.id), check.category, check.status, check.explanation,
                    )
                if burst_frames:
                    for index, frame in enumerate(burst_frames, 1):
                        cursor.execute(
                            "INSERT INTO dbo.recognition_burst_frames "
                            "(record_id, frame_index, duration_seconds, evidence) VALUES (?, ?, ?, ?)",
                            str(record.id), index, burst_duration_seconds, pyodbc.Binary(frame),
                        )
            connection.commit()
        except pyodbc.Error:
            connection.rollback()
            raise


def list_records(
    page: int, page_size: int, search: str, outcome: str | None,
    review_status: str | None, region: str | None,
) -> RecognitionPage:
    clauses, parameters = [], []
    if search:
        clauses.append("(CHARINDEX(?, shop_code) > 0 OR CHARINDEX(?, camera_name) > 0 "
                       "OR CHARINDEX(?, filename) > 0)")
        parameters.extend([search] * 3)
    for column, value in (("outcome", outcome), ("review_status", review_status), ("region", region)):
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            summary_row = cursor.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN outcome='flagged' THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN outcome='needs_review' THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN review_status='new' THEN 1 ELSE 0 END),0) "
                "FROM dbo.recognition_records"
            ).fetchone()
            summary = RecognitionSummary(
                total=summary_row[0], flagged=summary_row[1],
                needs_review=summary_row[2], unreviewed=summary_row[3],
            )
            total = cursor.execute(
                "SELECT COUNT(*) FROM dbo.recognition_records" + where, *parameters,
            ).fetchone()[0]
            rows = cursor.execute(
                f"SELECT {COLUMNS} FROM dbo.recognition_records{where} "
                "ORDER BY uploaded_at DESC, id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
                *parameters, (page - 1) * page_size, page_size,
            ).fetchall()
            records = _records(cursor, rows)
    return RecognitionPage(
        records=records, total=total, page=page, page_size=page_size, summary=summary,
    )


def get_record(record_id: UUID) -> RecognitionRecord | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            rows = cursor.execute(
                f"SELECT {COLUMNS} FROM dbo.recognition_records WHERE id = ?", str(record_id),
            ).fetchall()
            records = _records(cursor, rows)
            return records[0] if records else None


def get_evidence(record_id: UUID) -> bytes | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "SELECT evidence FROM dbo.recognition_records WHERE id = ?", str(record_id),
            ).fetchone()
            return bytes(row[0]) if row else None


def get_burst_info(record_id: UUID) -> tuple[int, float] | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "SELECT COUNT(f.frame_index), MAX(f.duration_seconds) "
                "FROM dbo.recognition_records r LEFT JOIN dbo.recognition_burst_frames f "
                "ON r.id = f.record_id WHERE r.id = ? GROUP BY r.id",
                str(record_id),
            ).fetchone()
            return (int(row[0]) + 1, float(row[1])) if row and row[0] else None


def get_burst_frame(record_id: UUID, frame_index: int) -> bytes | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "SELECT evidence FROM dbo.recognition_burst_frames WHERE record_id = ? AND frame_index = ?",
                str(record_id), frame_index,
            ).fetchone()
            return bytes(row[0]) if row else None


def approve_baseline(record_id: UUID) -> Baseline | None:
    approved_at = datetime.now(timezone.utc)
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "INSERT INTO dbo.recognition_baselines "
                "(record_id, region, shop_code, camera_name, approved_at) "
                "OUTPUT inserted.record_id, inserted.approved_at "
                "SELECT id, region, shop_code, camera_name, ? "
                "FROM dbo.recognition_records WHERE id = ?",
                approved_at.replace(tzinfo=None), str(record_id),
            ).fetchone()
            return Baseline(record_id=row[0], approved_at=row[1].replace(tzinfo=timezone.utc)) if row else None


def get_baseline(record_id: UUID) -> Baseline | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "SELECT TOP 1 b.record_id, b.approved_at "
                "FROM dbo.recognition_records r "
                "JOIN dbo.recognition_baselines b ON "
                "b.region COLLATE Latin1_General_100_BIN2 = r.region COLLATE Latin1_General_100_BIN2 "
                "AND b.shop_code COLLATE Latin1_General_100_BIN2 = r.shop_code COLLATE Latin1_General_100_BIN2 "
                "AND b.camera_name COLLATE Latin1_General_100_BIN2 = r.camera_name COLLATE Latin1_General_100_BIN2 "
                "WHERE r.id = ? ORDER BY b.id DESC",
                str(record_id),
            ).fetchone()
            return Baseline(record_id=row[0], approved_at=row[1].replace(tzinfo=timezone.utc)) if row else None


def get_baseline_evidence(region: str, shop_code: str, camera_name: str) -> bytes | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            row = cursor.execute(
                "SELECT TOP 1 r.evidence FROM dbo.recognition_baselines b "
                "JOIN dbo.recognition_records r ON r.id = b.record_id "
                "WHERE b.region COLLATE Latin1_General_100_BIN2 = ? COLLATE Latin1_General_100_BIN2 "
                "AND b.shop_code COLLATE Latin1_General_100_BIN2 = ? COLLATE Latin1_General_100_BIN2 "
                "AND b.camera_name COLLATE Latin1_General_100_BIN2 = ? COLLATE Latin1_General_100_BIN2 "
                "ORDER BY b.id DESC",
                region, shop_code, camera_name,
            ).fetchone()
            return bytes(row[0]) if row else None


def update_review(record_id: UUID, update: ReviewUpdate) -> RecognitionRecord | None:
    with closing(connect(DatabaseSettings())) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                "UPDATE dbo.recognition_records SET review_status = ?, review_note = ?, "
                "reviewed_at = ? WHERE id = ?",
                update.status, update.note.strip(),
                datetime.now(timezone.utc).replace(tzinfo=None)
                if update.status != "new" else None, str(record_id),
            )
    return get_record(record_id)
