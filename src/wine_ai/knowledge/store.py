import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_BATCH_SIZE = 100

ERROR_CATEGORIES = {
    "missing_module",
    "graphics",
    "memory",
    "startup",
    "unknown",
}

EVIDENCE_STATUSES = {
    "PROVEN",
    "PARTIALLY_PROVEN",
    "UNPROVEN",
    "CONTRADICTED",
    "BLOCKED",
}

CONFIDENCE_LEVELS = {"low", "medium", "high"}
GPU_VENDORS = {"intel", "amd", "nvidia", "unknown"}

SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ -]{0,63}$")


class KnowledgeStoreError(Exception):
    pass


def _validate_label(value, field_name):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not SAFE_LABEL.fullmatch(value):
        raise ValueError(f"Invalid {field_name}")
    if value != value.strip():
        raise ValueError(f"Invalid {field_name}")
    return value


def _validate_choice(value, allowed, field_name):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"Unsupported {field_name}")
    return value


class KnowledgeStore:
    """
    Local structured diagnostic storage.

    No raw logs, prompts, arbitrary text, file paths,
    executable contents, or prefix contents are accepted.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self):
        connection = self._connect()
        try:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]

            if version not in (0, SCHEMA_VERSION):
                raise KnowledgeStoreError(
                    f"Unsupported database schema version: {version}"
                )

            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS diagnostic_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        wine_version TEXT NOT NULL,
                        distro TEXT NOT NULL,
                        error_category TEXT NOT NULL,
                        evidence_status TEXT NOT NULL,
                        confidence TEXT NOT NULL,
                        gpu_vendor TEXT NOT NULL,
                        CHECK (error_category IN (
                            'missing_module',
                            'graphics',
                            'memory',
                            'startup',
                            'unknown'
                        )),
                        CHECK (evidence_status IN (
                            'PROVEN',
                            'PARTIALLY_PROVEN',
                            'UNPROVEN',
                            'CONTRADICTED',
                            'BLOCKED'
                        )),
                        CHECK (confidence IN (
                            'low', 'medium', 'high'
                        )),
                        CHECK (gpu_vendor IN (
                            'intel', 'amd', 'nvidia', 'unknown'
                        ))
                    )
                """)
                connection.execute(
                    f"PRAGMA user_version = {SCHEMA_VERSION}"
                )
        finally:
            connection.close()

        try:
            os.chmod(self.db_path, 0o600)
        except OSError as exc:
            raise KnowledgeStoreError(
                "Could not restrict database file permissions"
            ) from exc

    @staticmethod
    def _prepare_record(record):
        if not isinstance(record, dict):
            raise ValueError("Each record must be a dictionary")

        allowed_keys = {
            "wine_version",
            "distro",
            "error_category",
            "evidence_status",
            "confidence",
            "gpu_vendor",
        }

        if set(record) - allowed_keys:
            raise ValueError("Record contains unsupported fields")

        required_keys = {
            "wine_version",
            "distro",
            "error_category",
            "evidence_status",
            "confidence",
        }

        if not required_keys.issubset(record):
            raise ValueError("Record is missing required fields")

        wine_version = _validate_label(
            record["wine_version"], "wine_version"
        )
        distro = _validate_label(record["distro"], "distro")

        error_category = _validate_choice(
            record["error_category"],
            ERROR_CATEGORIES,
            "error_category",
        )
        evidence_status = _validate_choice(
            record["evidence_status"],
            EVIDENCE_STATUSES,
            "evidence_status",
        )
        confidence = _validate_choice(
            record["confidence"],
            CONFIDENCE_LEVELS,
            "confidence",
        )
        gpu_vendor = _validate_choice(
            record.get("gpu_vendor", "unknown"),
            GPU_VENDORS,
            "gpu_vendor",
        )

        created_at = datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")

        return (
            created_at,
            wine_version,
            distro,
            error_category,
            evidence_status,
            confidence,
            gpu_vendor,
        )

    def add_records_atomic(self, records):
        if not isinstance(records, list):
            raise ValueError("records must be a list")

        if len(records) > MAX_BATCH_SIZE:
            raise ValueError(
                f"Batch cannot exceed {MAX_BATCH_SIZE} records"
            )

        if not records:
            return []

        # Validate the complete batch before opening a write transaction.
        prepared = [
            self._prepare_record(record) for record in records
        ]

        connection = self._connect()
        record_ids = []

        try:
            with connection:
                for values in prepared:
                    cursor = connection.execute("""
                        INSERT INTO diagnostic_records (
                            created_at,
                            wine_version,
                            distro,
                            error_category,
                            evidence_status,
                            confidence,
                            gpu_vendor
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, values)

                    record_ids.append(cursor.lastrowid)

            return record_ids
        finally:
            connection.close()

    def add_record(
        self,
        *,
        wine_version,
        distro,
        error_category,
        evidence_status,
        confidence,
        gpu_vendor="unknown",
    ):
        record_ids = self.add_records_atomic([{
            "wine_version": wine_version,
            "distro": distro,
            "error_category": error_category,
            "evidence_status": evidence_status,
            "confidence": confidence,
            "gpu_vendor": gpu_vendor,
        }])

        return record_ids[0]

    def list_records(self, limit=100):
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("limit must be an integer from 1 to 1000")

        connection = self._connect()

        try:
            rows = connection.execute("""
                SELECT
                    id,
                    created_at,
                    wine_version,
                    distro,
                    error_category,
                    evidence_status,
                    confidence,
                    gpu_vendor
                FROM diagnostic_records
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()

            return [dict(row) for row in rows]
        finally:
            connection.close()
