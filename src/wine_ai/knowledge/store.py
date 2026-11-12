"""Hardened local diagnostic knowledge storage.

Security properties implemented here:

- The database file is created with ``O_CREAT | O_EXCL | O_NOFOLLOW``
  and owner-only permissions (0600) from the moment it appears.
- The immediate parent directory must not be a symbolic link, must be
  owned by the current user, and its permission bits are enforced to
  exactly 0700 (any looser OR narrower mode is corrected before
  SQLite opens the file).
- An existing database file and existing sidecar files are tightened
  to 0600 *before* SQLite opens them, not only after initialization.
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
- The structured-field guarantees are enforced twice: by the Python
  validation API *and* by SQL ``CHECK`` constraints inside SQLite
  itself (schema version 3 covers ``wine_version``, ``distro``,
  ``error_code`` and ``prefix_id`` in addition to the choice fields).
  A row written through a raw SQLite connection that violates any
  structured rule is rejected by the database engine.
- Records read back for export are re-validated against the same
  rules before leaving the store; a tampered database fails closed
  instead of exporting unstructured data.
- Schema version 2 added two optional columns (``error_code`` and
  ``prefix_id``). Version 3 rebuilds the table with full ``CHECK``
  coverage. Version 4 adds two further optional columns with their own
  ``CHECK`` coverage: ``launcher`` (an allow-listed launcher name, never
  a path) and ``prefix_hash`` (a machine-keyed HMAC-SHA256 pseudonym
  of the prefix path, produced by ``environment.snapshot.prefix_hash_of``:
  excluded from export and NOT anonymity or a secret against local
  processes; cloned machine identifiers can enable cross-image linkage,
  and truncated hashes have a non-zero collision probability;
  the raw path is never accepted or stored). Version 1, 2 and 3
  databases are upgraded by atomic migrations whose pre-checks (stored
  CREATE statement and column set) run *inside* the same ``BEGIN
  IMMEDIATE`` transaction as the migration itself; any mismatch or
  error rolls back and leaves the database untouched. The canonical
  schema is tracked in
  ``docs/decisions/2026-11-11-schema-canonical-v3-v4.md``.

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

SCHEMA_VERSION = 4
SCHEMA_VERSION_V3 = 3
SCHEMA_VERSION_V2 = 2
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

# Launcher names are abstract categorical labels (never paths, never
# version strings). ``unknown`` is the explicit "not detected" value.
LAUNCHERS = frozenset({
    "lutris", "bottles", "heroic", "steam", "wine", "unknown",
})

SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ -]{0,63}$")

# Prefix pseudonym: lowercase hexadecimal, 16..64 chars (currently a
# 128-bit truncated HMAC-SHA256 produced by the environment layer; the
# raw prefix path is never accepted here).
SAFE_HASH = re.compile(r"^[a-f0-9]{16,64}$")

TABLE_NAME = "diagnostic_records"

# Sidecar files SQLite can place next to the database file.
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

# Canonical schema (version 4). The sqlite_master SQL stored by
# databases created by this library matches this text after whitespace
# normalization (SQLite strips "IF NOT EXISTS" when storing the
# statement). Any other definition is refused as incompatible.
#
# The CHECK constraints mirror the Python-side validation rules so the
# database engine itself is the last line of defense: values written
# through a raw SQLite connection are enforced without this API.
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
    launcher TEXT,
    prefix_hash TEXT,
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
    )),
    CHECK (launcher IS NULL OR launcher IN (
        'lutris', 'bottles', 'heroic', 'steam', 'wine', 'unknown'
    )),
    CHECK (prefix_hash IS NULL OR (
        length(prefix_hash) BETWEEN 16 AND 64
        AND prefix_hash NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (length(wine_version) BETWEEN 1 AND 64
        AND substr(wine_version, 1, 1) GLOB '[A-Za-z0-9]'
        AND wine_version NOT GLOB '*[^A-Za-z0-9._+ -]*'
        AND wine_version = trim(wine_version)),
    CHECK (length(distro) BETWEEN 1 AND 64
        AND substr(distro, 1, 1) GLOB '[A-Za-z0-9]'
        AND distro NOT GLOB '*[^A-Za-z0-9._+ -]*'
        AND distro = trim(distro)),
    CHECK (prefix_id IS NULL OR (
        length(prefix_id) BETWEEN 1 AND 64
        AND substr(prefix_id, 1, 1) GLOB '[A-Za-z0-9]'
        AND prefix_id NOT GLOB '*[^A-Za-z0-9._+ -]*'
        AND prefix_id = trim(prefix_id))),
    CHECK (error_code IS NULL OR (
        (substr(error_code, 1, 2) = '0x'
            AND length(error_code) BETWEEN 3 AND 10
            AND substr(error_code, 3) NOT GLOB '*[^0-9A-F]*')
        OR (length(error_code) BETWEEN 2 AND 64
            AND substr(error_code, 1, 1) GLOB '[A-Z]'
            AND error_code NOT GLOB '*[^A-Z0-9_]*')
    ))
)
"""

# Version 3 canonical schema. Databases stamped with version 3 carry
# this stored CREATE statement; they are upgraded to version 4 by an
# atomic table rebuild (same pattern as the v2 -> v3 migration).
EXPECTED_TABLE_SQL_V3 = """
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
    )),
    CHECK (length(wine_version) BETWEEN 1 AND 64
        AND substr(wine_version, 1, 1) GLOB '[A-Za-z0-9]'
        AND wine_version NOT GLOB '*[^A-Za-z0-9._+ -]*'
        AND wine_version = trim(wine_version)),
    CHECK (length(distro) BETWEEN 1 AND 64
        AND substr(distro, 1, 1) GLOB '[A-Za-z0-9]'
        AND distro NOT GLOB '*[^A-Za-z0-9._+ -]*'
        AND distro = trim(distro)),
    CHECK (prefix_id IS NULL OR (
        length(prefix_id) BETWEEN 1 AND 64
        AND substr(prefix_id, 1, 1) GLOB '[A-Za-z0-9]'
        AND prefix_id NOT GLOB '*[^A-Za-z0-9._+ -]*'
        AND prefix_id = trim(prefix_id))),
    CHECK (error_code IS NULL OR (
        (substr(error_code, 1, 2) = '0x'
            AND length(error_code) BETWEEN 3 AND 10
            AND substr(error_code, 3) NOT GLOB '*[^0-9A-F]*')
        OR (length(error_code) BETWEEN 2 AND 64
            AND substr(error_code, 1, 1) GLOB '[A-Z]'
            AND error_code NOT GLOB '*[^A-Z0-9_]*')
    ))
)
"""

# Version 2 canonical schema. Databases stamped with version 2 carry
# this stored CREATE statement; they are upgraded to version 3 by an
# atomic table rebuild.
EXPECTED_TABLE_SQL_V2 = """
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
# it is accepted as long as the column set matches version 1 exactly.
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
    "launcher": ("TEXT", False, False),
    "prefix_hash": ("TEXT", False, False),
}

# Version 3 columns (the v3 -> v4 migration pre-check requires exactly
# this set before touching a version 3 database). Version 2 databases
# carry the same column set (their SQL differs only in CHECK coverage).
EXPECTED_COLUMNS_V3 = {
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

# Column set of a stored record as returned by the read API. Export
# re-validation refuses any row carrying extra or missing columns.
STORED_RECORD_COLUMNS = frozenset({
    "id",
    "created_at",
    "wine_version",
    "distro",
    "error_category",
    "evidence_status",
    "confidence",
    "gpu_vendor",
    "error_code",
    "prefix_id",
    "launcher",
    "prefix_hash",
})

_ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)


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


def validate_existing_record(record):
    """Validate a row as read back from the database.

    This is the fail-closed export gate: every record that leaves the
    store (e.g. via ``wine_ai.portability``) is checked against the
    same rules the write path enforces, plus the storage-only columns
    (``id`` and ``created_at``). A database tampered with through a
    raw SQLite connection therefore cannot export its rows; the
    export fails instead of emitting unstructured data.

    Raises ``ValueError`` on any violation; returns the record
    unchanged when valid.
    """
    if not isinstance(record, dict):
        raise ValueError("Stored record must be a dictionary")

    unknown = set(record) - STORED_RECORD_COLUMNS
    if unknown:
        raise ValueError(
            f"Stored record has unexpected columns: {sorted(unknown)}"
        )

    record_id = record.get("id")
    if (
        not isinstance(record_id, int)
        or isinstance(record_id, bool)
        or record_id < 1
    ):
        raise ValueError("Stored record id is invalid")

    created_at = record.get("created_at")
    if not isinstance(created_at, str) or not _ISO_TIMESTAMP.match(
        created_at
    ):
        raise ValueError("Stored record created_at is invalid")
    try:
        datetime.fromisoformat(created_at)
    except ValueError:
        raise ValueError("Stored record created_at is invalid") from None

    _validate_label(record.get("wine_version"), "wine_version")
    _validate_label(record.get("distro"), "distro")
    _validate_choice(
        record.get("error_category"),
        ERROR_CATEGORIES,
        "error_category",
    )
    _validate_choice(
        record.get("evidence_status"),
        EVIDENCE_STATUSES,
        "evidence_status",
    )
    _validate_choice(
        record.get("confidence"), CONFIDENCE_LEVELS, "confidence"
    )
    _validate_choice(record.get("gpu_vendor"), GPU_VENDORS, "gpu_vendor")

    error_code = record.get("error_code")
    if error_code is not None and normalize_error_code(error_code) != error_code:
        raise ValueError("Stored error_code is not in canonical form")

    prefix_id = record.get("prefix_id")
    if prefix_id is not None:
        _validate_label(prefix_id, "prefix_id")

    return record


class KnowledgeStore:
    """
    Local structured diagnostic storage.

    No raw logs, prompts, arbitrary text, file paths,
    executable contents, or prefix contents are accepted.

    Evidence statuses are caller-supplied classifications, not cryptographic
    proof. This store validates the status vocabulary and schema but does not
    verify evidence provenance or test artifacts. Direct low-level callers
    remain trusted to assign PROVEN honestly; recipe/bridge APIs deliberately
    refuse to issue PROVEN until a trusted verifier exists.

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
        # Enforce exactly 0700: a looser mode (0755) would expose the
        # database to group/other, and a narrower one (0500) would break
        # writing. Both are corrected to the required mode.
        if stat.S_IMODE(info.st_mode) & 0o777 != 0o700:
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
        # Tighten an existing sidecar *before* SQLite touches it.
        if stat.S_IMODE(info.st_mode) & 0o777 != 0o600:
            try:
                os.chmod(sidecar, 0o600)
            except OSError as exc:
                raise KnowledgeStoreError(
                    "Could not restrict sidecar file permissions: "
                    f"{sidecar.name}"
                ) from exc

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

        # Tighten an existing database file *before* SQLite opens it.
        if stat.S_IMODE(info.st_mode) & 0o777 != 0o600:
            try:
                os.chmod(self.db_path, 0o600)
            except OSError as exc:
                raise KnowledgeStoreError(
                    "Could not restrict database file permissions"
                ) from exc

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
        version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        expected_sql_by_version = {
            SCHEMA_VERSION: EXPECTED_TABLE_SQL,
            SCHEMA_VERSION_V3: EXPECTED_TABLE_SQL_V3,
            SCHEMA_VERSION_V2: EXPECTED_TABLE_SQL_V2,
            1: EXPECTED_TABLE_SQL_V1,
        }
        expected_sql = expected_sql_by_version.get(version)
        if expected_sql is None:
            raise KnowledgeStoreError(
                f"Unsupported database schema version: {version}"
            )

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
        if normalized != _normalize_sql(expected_sql):
            raise KnowledgeStoreError(
                "Schema verification failed: incompatible table definition"
            )

        expected_columns = (
            EXPECTED_COLUMNS_V1 if version == 1 else (
                EXPECTED_COLUMNS_V3
                if version in (SCHEMA_VERSION_V2, SCHEMA_VERSION_V3)
                else EXPECTED_COLUMNS
            )
        )
        actual = KnowledgeStore._read_columns(connection)
        if set(actual) != set(expected_columns):
            raise KnowledgeStoreError(
                "Schema verification failed: incompatible column set"
            )
        for name, expected in expected_columns.items():
            if actual[name] != expected:
                raise KnowledgeStoreError(
                    f"Schema verification failed: unexpected column {name}"
                )

    def _migrate_v1_to_v2(self, connection):
        """Atomically upgrade a verified version 1 database.

        All pre-checks run *inside* the ``BEGIN IMMEDIATE`` transaction,
        so no other process can alter the schema between verification
        and migration (the check-then-migrate window is closed). The
        stored CREATE statement must match the canonical version 1 text
        and the column set must be exactly the version 1 columns. Any
        failure rolls the migration back and leaves the database on
        version 1, unchanged.
        """
        try:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (TABLE_NAME,),
            ).fetchone()
            if row is None or not row[0]:
                raise KnowledgeStoreError(
                    "Schema migration failed: table is missing"
                )
            if _normalize_sql(row[0]) != _normalize_sql(
                EXPECTED_TABLE_SQL_V1
            ):
                raise KnowledgeStoreError(
                    "Schema migration failed: incompatible table definition"
                )

            actual = self._read_columns(connection)
            if set(actual) != set(EXPECTED_COLUMNS_V1):
                raise KnowledgeStoreError(
                    "Schema migration failed: incompatible column set"
                )

            connection.execute(
                f"ALTER TABLE {TABLE_NAME} ADD COLUMN error_code TEXT"
            )
            connection.execute(
                f"ALTER TABLE {TABLE_NAME} ADD COLUMN prefix_id TEXT"
            )
            connection.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION_V2}"
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise KnowledgeStoreError(
                "Schema migration failed: database left unchanged"
            ) from exc

    def _migrate_v2_to_v3(self, connection):
        """Atomically upgrade a verified version 2 database to version 3.

        SQLite cannot add a CHECK constraint to an existing table, so
        the upgrade rebuilds the table: rename the old table, create
        the canonical version 3 definition, copy every row, drop the
        backup, and stamp version 3 — all inside one
        ``BEGIN IMMEDIATE`` transaction whose pre-checks (stored SQL
        fingerprint and column set) also run inside the transaction.

        A row that violates the new CHECK constraints (possible only
        through raw SQLite writes into a version 2 database) makes the
        copy fail, which rolls the whole migration back and leaves the
        database on version 2, unchanged. Fail closed by design.
        """
        try:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (TABLE_NAME,),
            ).fetchone()
            if row is None or not row[0]:
                raise KnowledgeStoreError(
                    "Schema migration failed: table is missing"
                )
            if _normalize_sql(row[0]) != _normalize_sql(
                EXPECTED_TABLE_SQL_V2
            ):
                raise KnowledgeStoreError(
                    "Schema migration failed: incompatible table definition"
                )

            actual = self._read_columns(connection)
            if set(actual) != set(EXPECTED_COLUMNS_V3):
                raise KnowledgeStoreError(
                    "Schema migration failed: incompatible column set"
                )

            backup_name = f"{TABLE_NAME}_v2_backup"
            connection.execute(
                f"ALTER TABLE {TABLE_NAME} RENAME TO {backup_name}"
            )
            connection.execute(EXPECTED_TABLE_SQL_V3)
            connection.execute(f"""
                INSERT INTO {TABLE_NAME} (
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
                )
                SELECT
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
                FROM {backup_name}
            """)
            connection.execute(f"DROP TABLE {backup_name}")
            connection.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION_V3}"
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise KnowledgeStoreError(
                "Schema migration failed: database left unchanged"
            ) from exc

    def _migrate_v3_to_v4(self, connection):
        """Atomically upgrade a verified version 3 database to version 4.

        Same rebuild pattern as v2 -> v3: SQLite cannot add a CHECK
        constraint to an existing table, so the upgrade rebuilds it —
        rename the old table, create the canonical version 4 definition,
        copy every row (the two new columns start out NULL for migrated
        rows), drop the backup, and stamp version 4 — all inside one
        ``BEGIN IMMEDIATE`` transaction whose pre-checks (stored SQL
        fingerprint against the canonical version 3 statement and the
        version 3 column set) also run inside the transaction.

        Any failure rolls the migration back and leaves the database on
        version 3, unchanged. Fail closed by design.
        """
        try:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (TABLE_NAME,),
            ).fetchone()
            if row is None or not row[0]:
                raise KnowledgeStoreError(
                    "Schema migration failed: table is missing"
                )
            if _normalize_sql(row[0]) != _normalize_sql(
                EXPECTED_TABLE_SQL_V3
            ):
                raise KnowledgeStoreError(
                    "Schema migration failed: incompatible table definition"
                )

            actual = self._read_columns(connection)
            if set(actual) != set(EXPECTED_COLUMNS_V3):
                raise KnowledgeStoreError(
                    "Schema migration failed: incompatible column set"
                )

            backup_name = f"{TABLE_NAME}_v3_backup"
            connection.execute(
                f"ALTER TABLE {TABLE_NAME} RENAME TO {backup_name}"
            )
            connection.execute(EXPECTED_TABLE_SQL)
            connection.execute(f"""
                INSERT INTO {TABLE_NAME} (
                    id,
                    created_at,
                    wine_version,
                    distro,
                    error_category,
                    evidence_status,
                    confidence,
                    gpu_vendor,
                    error_code,
                    prefix_id,
                    launcher,
                    prefix_hash
                )
                SELECT
                    id,
                    created_at,
                    wine_version,
                    distro,
                    error_category,
                    evidence_status,
                    confidence,
                    gpu_vendor,
                    error_code,
                    prefix_id,
                    NULL,
                    NULL
                FROM {backup_name}
            """)
            connection.execute(f"DROP TABLE {backup_name}")
            connection.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION}"
            )
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

            if version not in (0, 1, SCHEMA_VERSION_V2, SCHEMA_VERSION_V3,
                               SCHEMA_VERSION):
                raise KnowledgeStoreError(
                    f"Unsupported database schema version: {version}"
                )

            table_exists = self._table_exists(connection)

            if version == 0 and table_exists:
                raise KnowledgeStoreError(
                    "Partial initialization detected: table exists but "
                    "schema version is not stamped"
                )
            if version in (
                1,
                SCHEMA_VERSION_V2,
                SCHEMA_VERSION_V3,
                SCHEMA_VERSION,
            ) and not table_exists:
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
                self._migrate_v2_to_v3(connection)
                self._migrate_v3_to_v4(connection)
            elif version == SCHEMA_VERSION_V2:
                self._migrate_v2_to_v3(connection)
                self._migrate_v3_to_v4(connection)
            elif version == SCHEMA_VERSION_V3:
                self._migrate_v3_to_v4(connection)

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
            "launcher",
            "prefix_hash",
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

        launcher = record.get("launcher")
        if launcher is not None:
            launcher = _validate_choice(launcher, LAUNCHERS, "launcher")

        prefix_hash = record.get("prefix_hash")
        if prefix_hash is not None:
            if (not isinstance(prefix_hash, str)
                    or not SAFE_HASH.fullmatch(prefix_hash)):
                raise ValueError("Invalid prefix_hash")

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
            launcher,
            prefix_hash,
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
                            prefix_id,
                            launcher,
                            prefix_hash
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        launcher=None,
        prefix_hash=None,
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
            "launcher": launcher,
            "prefix_hash": prefix_hash,
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
                prefix_id,
                launcher,
                prefix_hash
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

    def find_by_launcher(self, launcher, limit=QUERY_LIMIT_DEFAULT):
        """Records attributed to an allow-listed launcher name."""
        _validate_choice(launcher, LAUNCHERS, "launcher")
        return self._query_records("launcher = ?", launcher, limit)

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
