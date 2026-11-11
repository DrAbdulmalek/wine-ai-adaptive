"""Hardened local diagnostic knowledge storage.

Security properties implemented here:

- The database file is created with ``O_CREAT | O_EXCL | O_NOFOLLOW``
  and owner-only permissions (0600) from the moment it appears.
- The immediate parent directory must not be a symbolic link, must be
  owned by the current user, and is tightened to 0700 if group or
  other access is found.
- Symbolic links and non-regular files at the database path or at the
  SQLite sidecar paths (-wal, -shm, -journal) are rejected instead of
  being followed.
- The schema fingerprint (the stored CREATE statement plus the column
  metadata reported by ``PRAGMA table_info``) is verified on every
  open. A database stamped with a known schema version but carrying an
  incompatible table is refused.
- Fresh initialization (table creation plus version stamping) runs in
  a single transaction, and partially initialized databases are
  detected and refused instead of being silently completed.
- Sidecar files found next to the database are restricted to 0600.
- Schema version 2 adds two optional columns (``error_code`` and
  ``prefix_id``). Version 1 databases are upgraded by an atomic
  migration that first verifies the stored CREATE statement and the
  column set, and fails closed leaving the database untouched on any
  mismatch or error.

Documented limitations (these are NOT fully eliminated):

- Path components above the immediate parent directory are not
  symlink-checked.
- Check-then-use (TOCTOU) races cannot be fully removed from
  userspace; ``O_NOFOLLOW`` and pre-open validation narrow the race
  windows but do not close them completely.
- Runtime rollback-journal sidecars are created by SQLite using the
  process umask of the embedding application; for strict privacy on
  multi-user systems run the application with umask 077.
- Initialization briefly changes the process umask; initialize stores
  before relying on concurrent file creation in other threads.
"""

import errno
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
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

TABLE_NAME = "diagnostic_records"

# Sidecar files SQLite can place next to the database file.
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

# Canonical schema (version 2). The sqlite_master SQL stored by
# databases created by this library matches this text after whitespace
# normalization (SQLite strips "IF NOT EXISTS" when storing the
# statement). Any other definition is refused as incompatible.
EXPECTED_TABLE_SQL = """
CREATE TABLE diagnostic_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    wine_version TEXT NOT NULL,
    distro TEXT NOT NULL,
    error_category TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    gpu_vendor TEXT NOT NULL,
    error_code TEXT,
    prefix_id TEXT,
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
"""

# Version 1 canonical schema. Databases migrated from version 1 keep
# this stored CREATE statement (ALTER TABLE does not rewrite it), so
# it is accepted as long as the column set matches version 2 exactly.
EXPECTED_TABLE_SQL_V1 = """
CREATE TABLE diagnostic_records (
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
"""

# Column metadata as reported by PRAGMA table_info:
# (declared type, NOT NULL enforced, part of the primary key).
EXPECTED_COLUMNS = {
    "id": ("INTEGER", False, True),
    "created_at": ("TEXT", True, False),
    "wine_version": ("TEXT", True, False),
    "distro": ("TEXT", True, False),
    "error_category": ("TEXT", True, False),
    "evidence_status": ("TEXT", True, False),
    "confidence": ("TEXT", True, False),
    "gpu_vendor": ("TEXT", True, False),
    "error_code": ("TEXT", False, False),
    "prefix_id": ("TEXT", False, False),
}

# Version 1 columns (the migration pre-check requires exactly this set
# before touching a version 1 database).
EXPECTED_COLUMNS_V1 = {
    "id": ("INTEGER", False, True),
    "created_at": ("TEXT", True, False),
    "wine_version": ("TEXT", True, False),
    "distro": ("TEXT", True, False),
    "error_category": ("TEXT", True, False),
    "evidence_status": ("TEXT", True, False),
    "confidence": ("TEXT", True, False),
    "gpu_vendor": ("TEXT", True, False),
}

# Structured error-code identifier: either a hexadecimal code
# (HRESULT/NTSTATUS/exception) or an UPPER_SNAKE_CASE name
# (STATUS_*/ERROR_*). No paths, spaces or arbitrary text.
SAFE_CODE = re.compile(r"^(0x[0-9A-Fa-f]{1,8}|[A-Z][A-Z0-9_]{1,63})$")

MAX_LIST_LIMIT = 1000
QUERY_LIMIT_DEFAULT = 100
QUERY_LIMIT_MAX = 1000


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


def _validate_limit(limit, default=QUERY_LIMIT_DEFAULT):
    if limit is None:
        return default
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= MAX_LIST_LIMIT
    ):
        raise ValueError(
            f"limit must be an integer from 1 to {MAX_LIST_LIMIT}"
        )
    return limit


def normalize_error_code(value):
    """Validate and normalize an error-code identifier.

    Hexadecimal codes keep the lowercase ``0x`` prefix and gain
    uppercase hex digits; UPPER_SNAKE_CASE names are uppercased
    (lowercase names are accepted and normalized). Anything else is
    rejected.
    """
    if not isinstance(value, str):
        raise ValueError("Invalid error_code")
    if value.lower().startswith("0x"):
        candidate = "0x" + value[2:]
        if not SAFE_CODE.fullmatch(candidate):
            raise ValueError("Invalid error_code")
        return "0x" + value[2:].upper()
    candidate = value.upper()
    if not SAFE_CODE.fullmatch(candidate):
        raise ValueError("Invalid error_code")
    return candidate


def _normalize_sql(sql):
    return " ".join(sql.split())


class KnowledgeStore:
    """
    Local structured diagnostic storage.

    No raw logs, prompts, arbitrary text, file paths,
    executable contents, or prefix contents are accepted.

    See the module docstring for the exact filesystem and schema
    hardening properties, and for their documented limitations.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path).expanduser()
        umask = os.umask(0o077)
        try:
            self.db_path.parent.mkdir(
                parents=True,
                exist_ok=True,
                mode=0o700,
            )
            self._initialize()
        finally:
            os.umask(umask)

    def _connect(self):
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    # -- filesystem hardening -------------------------------------------

    def _verify_parent_directory(self):
        parent = self.db_path.parent
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise KnowledgeStoreError(
                "Database directory is a symbolic link"
            )
        if not stat.S_ISDIR(info.st_mode):
            raise KnowledgeStoreError(
                "Database directory is not a directory"
            )
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise KnowledgeStoreError(
                "Database directory is owned by a different user"
            )
        if stat.S_IMODE(info.st_mode) & 0o077:
            try:
                os.chmod(parent, 0o700)
            except OSError as exc:
                raise KnowledgeStoreError(
                    "Could not restrict database directory permissions"
                ) from exc

    def _create_database_file(self):
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(str(self.db_path), flags, 0o600)
        except FileExistsError:
            # Benign race with a concurrent initializer; the file is
            # fully re-validated by the caller below.
            return
        except OSError as exc:
            if exc.errno == getattr(errno, "ELOOP", None):
                raise KnowledgeStoreError(
                    "Database path is a symbolic link"
                ) from exc
            raise KnowledgeStoreError(
                "Could not create database file safely"
            ) from exc
        os.close(descriptor)

    def _sidecar_path(self, suffix):
        return Path(str(self.db_path) + suffix)

    def _verify_sidecar(self, suffix):
        sidecar = self._sidecar_path(suffix)
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise KnowledgeStoreError(
                f"Unsafe sidecar file detected: {sidecar.name}"
            )
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise KnowledgeStoreError(
                f"Sidecar file is owned by a different user: {sidecar.name}"
            )

    def _verify_database_file(self):
        try:
            info = self.db_path.lstat()
        except FileNotFoundError:
            self._create_database_file()
            try:
                info = self.db_path.lstat()
            except FileNotFoundError as exc:
                raise KnowledgeStoreError(
                    "Database file could not be created"
                ) from exc

        if stat.S_ISLNK(info.st_mode):
            raise KnowledgeStoreError("Database path is a symbolic link")
        if not stat.S_ISREG(info.st_mode):
            raise KnowledgeStoreError("Database path is not a regular file")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise KnowledgeStoreError(
                "Database file is owned by a different user"
            )

        for suffix in SIDECAR_SUFFIXES:
            self._verify_sidecar(suffix)

    def _restrict_file_permissions(self):
        try:
            os.chmod(self.db_path, 0o600)
        except OSError as exc:
            raise KnowledgeStoreError(
                "Could not restrict database file permissions"
            ) from exc
        for suffix in SIDECAR_SUFFIXES:
            sidecar = self._sidecar_path(suffix)
            try:
                os.chmod(sidecar, 0o600)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise KnowledgeStoreError(
                    "Could not restrict sidecar file permissions: "
                    f"{sidecar.name}"
                ) from exc

    # -- schema handling -------------------------------------------------

    @staticmethod
    def _table_exists(connection):
        row = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = ?",
            (TABLE_NAME,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _read_columns(connection):
        columns = connection.execute(
            f"PRAGMA table_info({TABLE_NAME})"
        ).fetchall()
        return {
            column[1]: (
                (column[2] or "").upper(),
                bool(column[3]),
                bool(column[5]),
            )
            for column in columns
        }

    @staticmethod
    def _verify_schema(connection):
        row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = ?",
            (TABLE_NAME,),
        ).fetchone()
        if row is None or not row[0]:
            raise KnowledgeStoreError(
                "Schema verification failed: table is missing"
            )
        normalized = _normalize_sql(row[0])
        if normalized not in (
            _normalize_sql(EXPECTED_TABLE_SQL),
            _normalize_sql(EXPECTED_TABLE_SQL_V1),
        ):
            raise KnowledgeStoreError(
                "Schema verification failed: incompatible table definition"
            )

        actual = KnowledgeStore._read_columns(connection)
        if set(actual) != set(EXPECTED_COLUMNS):
            raise KnowledgeStoreError(
                "Schema verification failed: incompatible column set"
            )
        for name, expected in EXPECTED_COLUMNS.items():
            if actual[name] != expected:
                raise KnowledgeStoreError(
                    f"Schema verification failed: unexpected column {name}"
                )

    def _migrate_v1_to_v2(self, connection):
        """Atomically upgrade a verified version 1 database.

        Pre-checks are strict: the stored CREATE statement must match
        the canonical version 1 text and the column set must be exactly
        the version 1 columns. Any failure rolls the migration back and
        leaves the database on version 1, unchanged.
        """
        row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = ?",
            (TABLE_NAME,),
        ).fetchone()
        if row is None or not row[0]:
            raise KnowledgeStoreError(
                "Schema migration failed: table is missing"
            )
        if _normalize_sql(row[0]) != _normalize_sql(EXPECTED_TABLE_SQL_V1):
            raise KnowledgeStoreError(
                "Schema migration failed: incompatible table definition"
            )

        actual = self._read_columns(connection)
        if set(actual) != set(EXPECTED_COLUMNS_V1):
            raise KnowledgeStoreError(
                "Schema migration failed: incompatible column set"
            )

        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"ALTER TABLE {TABLE_NAME} ADD COLUMN error_code TEXT"
            )
            connection.execute(
                f"ALTER TABLE {TABLE_NAME} ADD COLUMN prefix_id TEXT"
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise KnowledgeStoreError(
                "Schema migration failed: database left unchanged"
            ) from exc

    def _initialize(self):
        self._verify_parent_directory()
        self._verify_database_file()

        connection = self._connect()
        try:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]

            if version not in (0, 1, SCHEMA_VERSION):
                raise KnowledgeStoreError(
                    f"Unsupported database schema version: {version}"
                )

            table_exists = self._table_exists(connection)

            if version == 0 and table_exists:
                raise KnowledgeStoreError(
                    "Partial initialization detected: table exists but "
                    "schema version is not stamped"
                )
            if version in (1, SCHEMA_VERSION) and not table_exists:
                raise KnowledgeStoreError(
                    "Partial initialization detected: schema version is "
                    "stamped but table is missing"
                )

            if not table_exists:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(EXPECTED_TABLE_SQL)
                    connection.execute(
                        f"PRAGMA user_version = {SCHEMA_VERSION}"
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            elif version == 1:
                self._migrate_v1_to_v2(connection)

            self._verify_schema(connection)
        finally:
            connection.close()

        self._restrict_file_permissions()

    # -- records API ------------------------------------------------------

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
            "error_code",
            "prefix_id",
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

        error_code = record.get("error_code")
        if error_code is not None:
            error_code = normalize_error_code(error_code)

        prefix_id = record.get("prefix_id")
        if prefix_id is not None:
            prefix_id = _validate_label(prefix_id, "prefix_id")

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
            error_code,
            prefix_id,
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
                            gpu_vendor,
                            error_code,
                            prefix_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        error_code=None,
        prefix_id=None,
    ):
        record_ids = self.add_records_atomic([{
            "wine_version": wine_version,
            "distro": distro,
            "error_category": error_category,
            "evidence_status": evidence_status,
            "confidence": confidence,
            "gpu_vendor": gpu_vendor,
            "error_code": error_code,
            "prefix_id": prefix_id,
        }])

        return record_ids[0]

    _RECORD_COLUMNS = """
                id,
                created_at,
                wine_version,
                distro,
                error_category,
                evidence_status,
                confidence,
                gpu_vendor,
                error_code,
                prefix_id
    """

    def _query_records(self, clause, parameter, limit):
        """Read-only parameterized lookup shared by find_* helpers."""
        limit = _validate_limit(limit)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT
                    {self._RECORD_COLUMNS}
                FROM diagnostic_records
                WHERE {clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                (parameter, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def list_records(self, limit=100):
        limit = _validate_limit(limit)

        connection = self._connect()

        try:
            rows = connection.execute(f"""
                SELECT
                    {self._RECORD_COLUMNS}
                FROM diagnostic_records
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()

            return [dict(row) for row in rows]
        finally:
            connection.close()

    # -- read-only query layer -------------------------------------------

    def find_by_error_category(self, category, limit=QUERY_LIMIT_DEFAULT):
        """Records filtered by an allow-listed error category."""
        _validate_choice(category, ERROR_CATEGORIES, "error_category")
        return self._query_records("error_category = ?", category, limit)

    def find_by_gpu_vendor(self, vendor, limit=QUERY_LIMIT_DEFAULT):
        """Records filtered by an allow-listed GPU vendor."""
        _validate_choice(vendor, GPU_VENDORS, "gpu_vendor")
        return self._query_records("gpu_vendor = ?", vendor, limit)

    def find_by_evidence_status(
        self, status, limit=QUERY_LIMIT_DEFAULT
    ):
        """Records filtered by an allow-listed evidence status."""
        _validate_choice(status, EVIDENCE_STATUSES, "evidence_status")
        return self._query_records("evidence_status = ?", status, limit)

    def find_by_error_code(self, error_code, limit=QUERY_LIMIT_DEFAULT):
        """Records carrying a specific structured error code."""
        normalized = normalize_error_code(error_code)
        return self._query_records("error_code = ?", normalized, limit)

    def category_counts(self):
        """Record counts grouped by error category (all categories)."""
        connection = self._connect()
        try:
            rows = connection.execute("""
                SELECT error_category, COUNT(*) AS total
                FROM diagnostic_records
                GROUP BY error_category
                ORDER BY total DESC, error_category ASC
            """).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            connection.close()

    def correlate_error_codes(self, limit=QUERY_LIMIT_DEFAULT):
        """Structured error codes ranked by how often they recur."""
        limit = _validate_limit(limit)
        connection = self._connect()
        try:
            rows = connection.execute("""
                SELECT error_code, COUNT(*) AS total
                FROM diagnostic_records
                WHERE error_code IS NOT NULL
                GROUP BY error_code
                ORDER BY total DESC, error_code ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [
                {"error_code": row[0], "count": row[1]}
                for row in rows
            ]
        finally:
            connection.close()
