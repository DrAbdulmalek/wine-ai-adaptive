"""Independent security tests for KnowledgeStore.

These tests attack the storage layer directly: symlinked paths,
non-regular files, unsafe directory permissions, tampered schemas,
partial initialization states and SQLite sidecar files.

Only temporary databases are used; no user database is ever touched.
"""

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.store import (
    KnowledgeStore,
    KnowledgeStoreError,
    SCHEMA_VERSION,
)


def valid_record(**overrides):
    record = {
        "wine_version": "11.19",
        "distro": "Manjaro",
        "error_category": "graphics",
        "evidence_status": "PARTIALLY_PROVEN",
        "confidence": "low",
        "gpu_vendor": "amd",
    }
    record.update(overrides)
    return record


class KnowledgeSecurityTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.db_path = self.base / "test.sqlite3"
        self.store = KnowledgeStore(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_raw_db(self, name, create_sql, user_version):
        path = self.base / name
        connection = sqlite3.connect(str(path))
        try:
            if create_sql is not None:
                connection.execute(create_sql)
            connection.execute(f"PRAGMA user_version = {user_version}")
            connection.commit()
        finally:
            connection.close()
        return path

    # -- path and file-type attacks --------------------------------------

    def test_rejects_symlinked_database_file(self):
        self.store.add_record(**valid_record())

        victim = self.base / "victim.txt"
        victim.write_text("innocent content")
        link = self.base / "link.sqlite3"
        os.symlink(victim, link)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(link)

        self.assertEqual(victim.read_text(), "innocent content")
        self.assertEqual(len(KnowledgeStore(self.db_path).list_records()), 1)

    def test_rejects_dangling_symlink_database_file(self):
        missing = self.base / "dangling.sqlite3"
        os.symlink(self.base / "nowhere.sqlite3", missing)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(missing)

        self.assertFalse((self.base / "nowhere.sqlite3").exists())
        self.assertTrue(missing.is_symlink())

    def test_rejects_symlinked_parent_directory(self):
        real_dir = self.base / "real_dir"
        real_dir.mkdir(mode=0o700)
        link_dir = self.base / "link_dir"
        os.symlink(real_dir, link_dir)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(link_dir / "test.sqlite3")

    def test_rejects_directory_in_place_of_database(self):
        fake = self.base / "fake.sqlite3"
        fake.mkdir()

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(fake)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX-only test")
    def test_rejects_non_regular_database_file(self):
        fifo = self.base / "pipe.sqlite3"
        os.mkfifo(fifo)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(fifo)

    # -- directory permissions and ownership -------------------------------

    def test_tightens_insecure_parent_directory_permissions(self):
        loose = self.base / "loose_dir"
        loose.mkdir(mode=0o700)
        os.chmod(loose, 0o777)

        store = KnowledgeStore(loose / "test.sqlite3")
        store.add_record(**valid_record())

        mode = stat.S_IMODE(loose.lstat().st_mode)
        self.assertEqual(mode & 0o077, 0)
        self.assertEqual(len(store.list_records()), 1)

    # -- schema attacks -----------------------------------------------------

    def test_rejects_versioned_database_without_table(self):
        path = self.make_raw_db("empty.sqlite3", None, SCHEMA_VERSION)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

    def test_rejects_schema_without_check_constraints(self):
        create_sql = """
            CREATE TABLE diagnostic_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                wine_version TEXT NOT NULL,
                distro TEXT NOT NULL,
                error_category TEXT NOT NULL,
                evidence_status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                gpu_vendor TEXT NOT NULL
            )
        """
        path = self.make_raw_db(
            "no_checks.sqlite3", create_sql, SCHEMA_VERSION
        )

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

    def test_rejects_schema_with_extra_column(self):
        create_sql = """
            CREATE TABLE diagnostic_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                wine_version TEXT NOT NULL,
                distro TEXT NOT NULL,
                error_category TEXT NOT NULL,
                evidence_status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                gpu_vendor TEXT NOT NULL,
                raw_log TEXT
            )
        """
        path = self.make_raw_db(
            "extra_column.sqlite3", create_sql, SCHEMA_VERSION
        )

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

    def test_rejects_schema_with_missing_column(self):
        create_sql = """
            CREATE TABLE diagnostic_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                wine_version TEXT NOT NULL,
                distro TEXT NOT NULL,
                error_category TEXT NOT NULL,
                evidence_status TEXT NOT NULL,
                confidence TEXT NOT NULL
            )
        """
        path = self.make_raw_db(
            "missing_column.sqlite3", create_sql, SCHEMA_VERSION
        )

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

    def test_rejects_partial_initialization(self):
        create_sql = "CREATE TABLE diagnostic_records (id INTEGER PRIMARY KEY)"
        path = self.make_raw_db("partial.sqlite3", create_sql, 0)

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(path)

    # -- healthy databases must survive -------------------------------------

    def test_healthy_database_is_preserved(self):
        self.store.add_records_atomic([
            valid_record(),
            valid_record(error_category="memory"),
        ])

        reopened = KnowledgeStore(self.db_path)
        self.assertEqual(len(reopened.list_records()), 2)

        reopened.add_record(**valid_record(error_category="startup"))
        self.assertEqual(
            len(KnowledgeStore(self.db_path).list_records()), 3
        )

    # -- SQLite sidecar files -------------------------------------------------

    def test_stale_sidecar_files_are_cleaned_or_restricted(self):
        # SQLite removes stale sidecars of non-WAL databases during
        # recovery; any sidecar that survives must end up owner-only.
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.db_path) + suffix)
            sidecar.write_bytes(b"stale")
            os.chmod(sidecar, 0o644)

        KnowledgeStore(self.db_path)

        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists():
                mode = stat.S_IMODE(sidecar.lstat().st_mode)
                self.assertEqual(
                    mode & 0o077, 0, f"sidecar {suffix} too open"
                )

    def test_no_persistent_sidecars_after_writes(self):
        self.store.add_record(**valid_record())

        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.db_path) + suffix)
            self.assertFalse(sidecar.exists(), f"sidecar {suffix} persisted")

    def test_wal_mode_database_remains_usable(self):
        self.store.add_record(**valid_record())

        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.commit()
        finally:
            connection.close()

        reopened = KnowledgeStore(self.db_path)
        previous_umask = os.umask(0o077)
        try:
            reopened.add_record(**valid_record(error_category="memory"))
        finally:
            os.umask(previous_umask)

        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists():
                mode = stat.S_IMODE(sidecar.lstat().st_mode)
                self.assertEqual(mode & 0o077, 0)

        self.assertEqual(
            len(KnowledgeStore(self.db_path).list_records()), 2
        )


if __name__ == "__main__":
    unittest.main()
