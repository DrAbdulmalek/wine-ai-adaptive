"""Tests for the schema migration chain (v1 -> v2 -> v3) and its guarantees.

All databases are temporary; no user database is ever touched.
"""

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.store import (
    EXPECTED_TABLE_SQL,
    EXPECTED_TABLE_SQL_V2,
    KnowledgeStore,
    KnowledgeStoreError,
    SCHEMA_VERSION,
    _normalize_sql,
)


V1_CREATE_SQL = """
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

# Canonical version 2 definition (mirrors EXPECTED_TABLE_SQL_V2).
V2_CREATE_SQL = EXPECTED_TABLE_SQL_V2


class SchemaMigrationTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_v2_database(self, name="v2.sqlite3", rows=2, poison=None):
        """Create a canonical version 2 database for migration tests.

        ``poison`` inserts one extra row whose ``wine_version`` violates
        the version 3 CHECK constraints — possible only through raw
        SQLite writes, which is exactly the scenario the v3 migration
        must fail closed on.
        """
        path = self.base / name
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(V2_CREATE_SQL)
            for index in range(rows):
                connection.execute(
                    """
                    INSERT INTO diagnostic_records (
                        created_at, wine_version, distro,
                        error_category, evidence_status,
                        confidence, gpu_vendor
                    )
                    VALUES ('2026-01-01T00:00:00+00:00', '10.15',
                            'Arch', 'graphics', 'PARTIALLY_PROVEN',
                            'low', 'amd')
                    """
                )
            if poison is not None:
                connection.execute(
                    """
                    INSERT INTO diagnostic_records (
                        created_at, wine_version, distro,
                        error_category, evidence_status,
                        confidence, gpu_vendor
                    )
                    VALUES ('2026-01-01T00:00:00+00:00', ?,
                            'Arch', 'graphics', 'PARTIALLY_PROVEN',
                            'low', 'amd')
                    """,
                    (poison,),
                )
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        finally:
            connection.close()
        return path

    def make_v1_database(self, name="v1.sqlite3", rows=2):
        path = self.base / name
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(V1_CREATE_SQL)
            for index in range(rows):
                connection.execute(
                    """
                    INSERT INTO diagnostic_records (
                        created_at, wine_version, distro,
                        error_category, evidence_status,
                        confidence, gpu_vendor
                    )
                    VALUES ('2026-01-01T00:00:00+00:00', '10.15',
                            'Arch', 'graphics', 'PARTIALLY_PROVEN',
                            'low', 'amd')
                    """
                )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        return path

    def raw_version(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
        finally:
            connection.close()

    def raw_column_names(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(diagnostic_records)"
                )
            ]
        finally:
            connection.close()

    def raw_row_count(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM diagnostic_records"
            ).fetchone()[0]
        finally:
            connection.close()

    def raw_stored_sql(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'diagnostic_records'"
            ).fetchone()[0]
        finally:
            connection.close()

    # -- successful migration ------------------------------------------------

    def test_v1_database_migrates_and_preserves_rows(self):
        path = self.make_v1_database(rows=3)

        store = KnowledgeStore(path)
        records = store.list_records()

        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)
        self.assertEqual(len(records), 3)
        self.assertEqual(
            set(records[0]),
            {
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
            },
        )
        self.assertIsNone(records[0]["error_code"])
        self.assertIsNone(records[0]["prefix_id"])

        store.add_record(
            wine_version="11.19",
            distro="Manjaro",
            error_category="memory",
            evidence_status="PARTIALLY_PROVEN",
            confidence="high",
            error_code="0xC0000005",
        )
        self.assertEqual(len(store.list_records()), 4)

    def test_migration_runs_once(self):
        path = self.make_v1_database()
        KnowledgeStore(path)
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)

        # Reopening a migrated database must not touch the data.
        reopened = KnowledgeStore(path)
        self.assertEqual(len(reopened.list_records()), 2)

    def test_v1_chain_preserves_ids_and_continues_sequence(self):
        path = self.make_v1_database(rows=3)
        store = KnowledgeStore(path)
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)
        self.assertEqual(
            [record["id"] for record in store.list_records()],
            [3, 2, 1],
        )

        next_id = store.add_record(
            wine_version="11.19",
            distro="Manjaro",
            error_category="memory",
            evidence_status="PARTIALLY_PROVEN",
            confidence="high",
        )
        self.assertEqual(next_id, 4)

    def test_v2_database_migrates_to_v3_preserving_rows_and_ids(self):
        path = self.make_v2_database(rows=3)

        store = KnowledgeStore(path)
        records = store.list_records()

        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)
        self.assertEqual(len(records), 3)
        self.assertEqual(
            [record["id"] for record in records], [3, 2, 1]
        )

        # The rebuilt table carries the canonical v3 fingerprint.
        self.assertEqual(
            _normalize_sql(self.raw_stored_sql(path)),
            _normalize_sql(EXPECTED_TABLE_SQL),
        )

        # AUTOINCREMENT sequence survived the rebuild.
        next_id = store.add_record(
            wine_version="11.19",
            distro="Manjaro",
            error_category="memory",
            evidence_status="PARTIALLY_PROVEN",
            confidence="high",
        )
        self.assertEqual(next_id, 4)

    # -- failed migrations must leave the database untouched -----------------

    def test_loose_file_permissions_are_tightened_before_migration(self):
        # A 0444 database file would previously make the migration fail.
        # The store now tightens owner-readable/writable permissions
        # *before* SQLite opens the file, so the migration proceeds and
        # the file ends at 0600.
        path = self.make_v1_database()
        os.chmod(path, 0o444)

        store = KnowledgeStore(path)

        mode = stat.S_IMODE(path.lstat().st_mode) & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(len(store.list_records()), 2)
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)

    def test_v2_to_v3_migration_rejects_poisoned_rows_atomically(self):
        # A v2 database may contain rows that violate the v3 CHECK
        # constraints (writable only through raw SQLite). The migration
        # must fail closed: rollback, version stays 2, rows untouched.
        path = self.make_v2_database(rows=2, poison="../evil")
        before_sql = self.raw_stored_sql(path)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

        self.assertEqual(self.raw_version(path), 2)
        self.assertEqual(len(self.raw_column_names(path)), 10)
        self.assertEqual(self.raw_row_count(path), 3)
        self.assertEqual(self.raw_stored_sql(path), before_sql)

        # Removing the poison lets the migration succeed normally.
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "DELETE FROM diagnostic_records "
                "WHERE wine_version = '../evil'"
            )
            connection.commit()
        finally:
            connection.close()

        store = KnowledgeStore(path)
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)
        self.assertEqual(len(store.list_records()), 2)

    def test_migration_failure_is_atomic(self):
        # The tampered-fingerprint case: the migration must abort inside
        # its transaction and leave the v1 database untouched (see
        # test_tampered_v1_sql_is_refused_without_migration for the
        # equivalent end-state assertions and the poisoned-row rollback
        # test above for a mid-copy failure injection).
        path = self.make_v1_database()
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "ALTER TABLE diagnostic_records ADD COLUMN raw_log TEXT"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

        self.assertEqual(self.raw_version(path), 1)
        self.assertEqual(len(self.raw_column_names(path)), 9)

        # A clean v1 database migrates fine afterwards.
        store = KnowledgeStore(self.make_v1_database(name="clean.sqlite3"))
        self.assertEqual(len(store.list_records()), 2)
        self.assertEqual(
            self.raw_version(store.db_path), SCHEMA_VERSION
        )

    def test_tampered_v1_sql_is_refused_without_migration(self):
        path = self.make_v1_database()
        # Weaken the CHECK constraints in the stored definition.
        connection = sqlite3.connect(str(path))
        try:
            connection.executescript(
                """
                PRAGMA writable_schema = ON;
                UPDATE sqlite_master
                SET sql = replace(sql, 'missing_module', 'missing_module2')
                WHERE name = 'diagnostic_records';
                PRAGMA writable_schema = OFF;
                """
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

        self.assertEqual(self.raw_version(path), 1)
        self.assertEqual(len(self.raw_column_names(path)), 8)

    def test_v1_with_extra_column_is_refused(self):
        path = self.make_v1_database()
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "ALTER TABLE diagnostic_records ADD COLUMN raw_log TEXT"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

        self.assertEqual(self.raw_version(path), 1)

    def test_unsupported_future_version_is_refused_unchanged(self):
        path = self.make_v1_database()
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("PRAGMA user_version = 999")
            connection.commit()
        finally:
            connection.close()

        before = path.read_bytes()
        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.raw_version(path), 999)

    def test_v1_sql_with_v2_stamp_but_missing_columns_is_refused(self):
        path = self.make_v1_database()
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

        # Refused without adding the missing columns.
        self.assertEqual(len(self.raw_column_names(path)), 8)

    # -- permission guarantees ------------------------------------------------

    def test_umask_does_not_weaken_permissions(self):
        previous_umask = os.umask(0o022)
        try:
            store = KnowledgeStore(self.base / "umask.sqlite3")
            store.add_record(
                wine_version="11.19",
                distro="Manjaro",
                error_category="graphics",
                evidence_status="PARTIALLY_PROVEN",
                confidence="low",
            )
        finally:
            os.umask(previous_umask)

        db_path = self.base / "umask.sqlite3"
        self.assertEqual(
            stat.S_IMODE(db_path.lstat().st_mode) & 0o777, 0o600
        )
        self.assertEqual(
            stat.S_IMODE(db_path.parent.lstat().st_mode) & 0o777, 0o700
        )


if __name__ == "__main__":
    unittest.main()
