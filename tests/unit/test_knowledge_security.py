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

    def test_parent_directory_mode_is_enforced_to_exactly_0700(self):
        # A 0500 directory passes the old "no group/other bits" check
        # but is not the required 0700 mode; the store must correct it
        # before opening the database.
        narrow = self.base / "narrow_dir"
        narrow.mkdir(mode=0o700)
        db_path = narrow / "test.sqlite3"

        # Create a healthy database first (needs a writable directory).
        store = KnowledgeStore(db_path)
        store.add_record(**valid_record())

        os.chmod(narrow, 0o500)
        try:
            reopened = KnowledgeStore(db_path)
            mode = stat.S_IMODE(narrow.lstat().st_mode) & 0o777
            self.assertEqual(mode, 0o700)
            self.assertEqual(len(reopened.list_records()), 1)
        finally:
            os.chmod(narrow, 0o700)

    def test_existing_database_file_is_tightened_before_use(self):
        self.store.add_record(**valid_record())
        os.chmod(self.db_path, 0o644)

        reopened = KnowledgeStore(self.db_path)

        mode = stat.S_IMODE(self.db_path.lstat().st_mode) & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(len(reopened.list_records()), 1)

    # -- SQL-level CHECK enforcement (last line of defense) -----------------

    def test_sqlite_engine_rejects_invalid_labels_without_python_api(self):
        bad_versions = [
            "../evil",          # path-like
            " led",             # leading space
            "x" * 65,           # over length cap
            "tab\there",        # control character
            "semi;colon",       # shell-ish punctuation
        ]
        for bad in bad_versions:
            with self.subTest(value=bad):
                connection = sqlite3.connect(str(self.db_path))
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            INSERT INTO diagnostic_records (
                                created_at, wine_version, distro,
                                error_category, evidence_status,
                                confidence, gpu_vendor
                            )
                            VALUES ('2026-01-01T00:00:00+00:00', ?,
                                    'Arch', 'graphics', 'UNPROVEN',
                                    'low', 'amd')
                            """,
                            (bad,),
                        )
                finally:
                    connection.close()

    def test_sqlite_engine_rejects_invalid_error_code_and_prefix(self):
        connection = sqlite3.connect(str(self.db_path))
        try:
            bad_codes = [
                "drop table",     # lower case + spaces
                "0xGG",           # non-hex digits
                "0xc0000005",     # lowercase hex (non-canonical)
                "0x123456789",    # too long
                "x" * 65,         # over length cap
            ]
            for bad in bad_codes:
                with self.subTest(error_code=bad):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            INSERT INTO diagnostic_records (
                                created_at, wine_version, distro,
                                error_category, evidence_status,
                                confidence, gpu_vendor, error_code
                            )
                            VALUES ('2026-01-01T00:00:00+00:00', '11.19',
                                    'Arch', 'graphics', 'UNPROVEN',
                                    'low', 'amd', ?)
                            """,
                            (bad,),
                        )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO diagnostic_records (
                        created_at, wine_version, distro,
                        error_category, evidence_status,
                        confidence, gpu_vendor, prefix_id
                    )
                    VALUES ('2026-01-01T00:00:00+00:00', '11.19',
                            'Arch', 'graphics', 'UNPROVEN',
                            'low', 'amd', '../evil')
                    """
                )
        finally:
            connection.close()

    def test_sqlite_engine_accepts_canonical_values(self):
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute(
                """
                INSERT INTO diagnostic_records (
                    created_at, wine_version, distro,
                    error_category, evidence_status,
                    confidence, gpu_vendor, error_code, prefix_id
                )
                VALUES ('2026-01-01T00:00:00+00:00', 'Wine 9.0',
                        'Manjaro Linux', 'graphics', 'UNPROVEN',
                        'low', 'amd', '0xC0000005', 'game-prefix')
                """
            )
            connection.commit()
            count = connection.execute(
                "SELECT COUNT(*) FROM diagnostic_records"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            connection.close()

    # -- export re-validation gate -------------------------------------------

    def test_validate_existing_record_accepts_healthy_row(self):
        from wine_ai.knowledge.store import validate_existing_record

        self.store.add_record(
            **valid_record(error_code="0xC0000005", prefix_id="p1")
        )
        row = self.store.list_records()[0]
        self.assertIs(validate_existing_record(row), row)

    def test_validate_existing_record_rejects_tampered_rows(self):
        from wine_ai.knowledge.store import validate_existing_record

        self.store.add_record(**valid_record())
        row = self.store.list_records()[0]

        tampered = dict(row, created_at="not-a-timestamp")
        with self.assertRaises(ValueError):
            validate_existing_record(tampered)

        tampered = dict(row, wine_version="../evil")
        with self.assertRaises(ValueError):
            validate_existing_record(tampered)

        tampered = dict(row, error_category="bogus")
        with self.assertRaises(ValueError):
            validate_existing_record(tampered)

        tampered = dict(row, error_code="0xc0000005")  # non-canonical
        with self.assertRaises(ValueError):
            validate_existing_record(tampered)

        tampered = dict(row, raw_log="smuggled column")
        with self.assertRaises(ValueError):
            validate_existing_record(tampered)

        tampered = dict(row)
        del tampered["id"]
        with self.assertRaises(ValueError):
            validate_existing_record(tampered)

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

    def test_rejects_symlinked_sidecar_files(self):
        # A sidecar path (-wal/-shm/-journal) holding a symbolic link
        # must be rejected instead of followed, and the symlink target
        # must remain untouched.
        self.store.add_record(**valid_record())

        victim = self.base / "victim_sidecar.txt"
        victim.write_text("innocent content")

        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.db_path) + suffix)
            self.assertFalse(sidecar.exists())
            os.symlink(victim, sidecar)

            try:
                with self.assertRaises(KnowledgeStoreError):
                    KnowledgeStore(self.db_path)
            finally:
                self.assertTrue(sidecar.is_symlink())
                os.unlink(sidecar)

            self.assertEqual(victim.read_text(), "innocent content")

        # After removing the symlinks the store must open normally.
        reopened = KnowledgeStore(self.db_path)
        self.assertEqual(len(reopened.list_records()), 1)

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
