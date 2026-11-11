"""Tests for schema version 4 and the v3 -> v4 migration guarantees.

Adapted from the independent v0.3.1 review package's persistence
regression suite (reopen-fresh, migration id-continuity, invalid legacy
row fail-closed, launcher CHECK enforcement), reimplemented against the
canonical rebuild-migration pattern used by this repository.

All databases are temporary; no user database is ever touched.
"""

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.store import (
    EXPECTED_TABLE_SQL_V1,
    EXPECTED_TABLE_SQL_V3,
    KnowledgeStore,
    KnowledgeStoreError,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V3,
    _normalize_sql,
)


# Canonical version 3 definition (mirrors EXPECTED_TABLE_SQL_V3).
V3_CREATE_SQL = EXPECTED_TABLE_SQL_V3

# Canonical version 1 definition (migration pre-check matches this
# text exactly after normalization).
V1_CREATE_SQL = EXPECTED_TABLE_SQL_V1

REC = {
    "wine_version": "11.19",
    "distro": "Manjaro",
    "error_category": "graphics",
    "evidence_status": "PARTIALLY_PROVEN",
    "confidence": "low",
}


class SchemaV4Tests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        os.chmod(self.base, 0o700)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_v3_database(self, name="v3.sqlite3", rows=1):
        path = self.base / name
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(V3_CREATE_SQL)
            for _ in range(rows):
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
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        finally:
            connection.close()
        os.chmod(path, 0o600)
        return path

    def raw_version(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()

    def raw_count(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM diagnostic_records"
            ).fetchone()[0]
        finally:
            connection.close()

    # -- fresh v4 ------------------------------------------------------------

    def test_reopen_fresh_v4(self):
        db = self.base / "fresh.sqlite3"
        store = KnowledgeStore(db)
        store.add_record(**REC)
        del store
        # Reopening a freshly created v4 database must not raise.
        store2 = KnowledgeStore(db)
        self.assertEqual(len(store2.list_records()), 1)
        store2.add_record(**{**REC, "error_category": "memory"})
        self.assertEqual(len(store2.list_records()), 2)
        self.assertEqual(self.raw_version(db), SCHEMA_VERSION)

    # -- v3 -> v4 migration --------------------------------------------------

    def test_v3_migration_preserves_rows_and_ids(self):
        path = self.make_v3_database(rows=2)
        store = KnowledgeStore(path)
        records = store.list_records()
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)
        self.assertEqual(len(records), 2)
        # Migrated rows carry NULL for the two new columns.
        self.assertIsNone(records[0]["launcher"])
        self.assertIsNone(records[0]["prefix_hash"])
        # AUTOINCREMENT continuity: the next insert continues the ids.
        nid = store.add_record(**REC)
        self.assertEqual(nid, 3)

    def test_v3_migration_rejects_tampered_fingerprint(self):
        path = self.make_v3_database(rows=1)
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "ALTER TABLE diagnostic_records RENAME TO diagnostic_records_x"
            )
            connection.execute(
                "CREATE TABLE diagnostic_records AS "
                "SELECT * FROM diagnostic_records_x"
            )
            connection.execute("DROP TABLE diagnostic_records_x")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION_V3)
        self.assertEqual(self.raw_count(path), 1)

    def test_migrated_database_enforces_launcher_check(self):
        path = self.make_v3_database(rows=1)
        KnowledgeStore(path)
        connection = sqlite3.connect(str(path))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO diagnostic_records (
                        created_at, wine_version, distro,
                        error_category, evidence_status,
                        confidence, gpu_vendor, launcher
                    )
                    VALUES ('2026-01-01T00:00:00+00:00', '10.15',
                            'Arch', 'graphics', 'PARTIALLY_PROVEN',
                            'low', 'amd', 'BOGUS')
                    """
                )
        finally:
            connection.close()

    def test_migrated_database_enforces_prefix_hash_check(self):
        path = self.make_v3_database(rows=1)
        KnowledgeStore(path)
        connection = sqlite3.connect(str(path))
        try:
            # Uppercase hex, too short and non-hex shapes all fail.
            for bad_hash in ("ABCDEF0123456789", "abcd", "zzzz111122223333"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO diagnostic_records (
                            created_at, wine_version, distro,
                            error_category, evidence_status,
                            confidence, gpu_vendor, prefix_hash
                        )
                        VALUES ('2026-01-01T00:00:00+00:00', '10.15',
                                'Arch', 'graphics', 'PARTIALLY_PROVEN',
                                'low', 'amd', ?)
                        """,
                        (bad_hash,),
                    )
        finally:
            connection.close()

    def test_v1_chain_reaches_v4(self):
        """The full v1 -> v2 -> v3 -> v4 chain works on open."""
        path = self.base / "v1chain.sqlite3"
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(V1_CREATE_SQL)
            connection.execute(
                "INSERT INTO diagnostic_records (created_at, wine_version,"
                " distro, error_category, evidence_status, confidence,"
                " gpu_vendor) VALUES ('2026-01-01T00:00:00+00:00', '10.15',"
                " 'Arch', 'graphics', 'PARTIALLY_PROVEN', 'low', 'amd')"
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        os.chmod(path, 0o600)

        store = KnowledgeStore(path)
        self.assertEqual(self.raw_version(path), SCHEMA_VERSION)
        records = store.list_records()
        self.assertEqual(len(records), 1)
        nid = store.add_record(**REC)
        self.assertEqual(nid, 2)

    # -- new columns through the API ------------------------------------------

    def test_add_record_persists_launcher_and_prefix_hash(self):
        db = self.base / "api.sqlite3"
        store = KnowledgeStore(db)
        store.add_record(
            **REC, launcher="steam",
            prefix_hash="0123456789abcdef0123456789abcdef",
        )
        record = store.list_records()[0]
        self.assertEqual(record["launcher"], "steam")
        self.assertEqual(
            record["prefix_hash"], "0123456789abcdef0123456789abcdef"
        )
        # The allow-listed query layer sees it too.
        self.assertEqual(len(store.find_by_launcher("steam")), 1)
        self.assertEqual(len(store.find_by_launcher("lutris")), 0)

    def test_add_record_rejects_invalid_launcher(self):
        db = self.base / "badlauncher.sqlite3"
        store = KnowledgeStore(db)
        with self.assertRaises(ValueError):
            store.add_record(**REC, launcher="/home/user/.wine")
        with self.assertRaises(ValueError):
            store.add_record(**REC, launcher="Steam")
        self.assertEqual(store.list_records(), [])

    def test_add_record_rejects_invalid_prefix_hash(self):
        db = self.base / "badhash.sqlite3"
        store = KnowledgeStore(db)
        with self.assertRaises(ValueError):
            # A raw path is never a valid pseudonym.
            store.add_record(**REC, prefix_hash="/home/user/.wine")
        with self.assertRaises(ValueError):
            store.add_record(**REC, prefix_hash="SHORT")
        self.assertEqual(store.list_records(), [])

    def test_find_by_launcher_rejects_unknown_names(self):
        db = self.base / "query.sqlite3"
        store = KnowledgeStore(db)
        with self.assertRaises(ValueError):
            store.find_by_launcher("not-a-launcher")


if __name__ == "__main__":
    unittest.main()
