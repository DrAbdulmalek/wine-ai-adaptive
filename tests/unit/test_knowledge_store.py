import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.knowledge.store import (
    KnowledgeStore,
    KnowledgeStoreError,
    SCHEMA_VERSION,
)


class KnowledgeStoreTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.store = KnowledgeStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def valid_record(self):
        return {
            "wine_version": "11.19",
            "distro": "Manjaro",
            "error_category": "graphics",
            "evidence_status": "PARTIALLY_PROVEN",
            "confidence": "low",
            "gpu_vendor": "amd",
        }

    def test_creates_database(self):
        self.assertTrue(self.db.is_file())

    def test_database_permissions_are_restricted(self):
        mode = self.db.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_schema_version(self):
        connection = sqlite3.connect(self.db)
        try:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(version, SCHEMA_VERSION)

    def test_inserts_structured_record(self):
        record_id = self.store.add_record(**self.valid_record())
        records = self.store.list_records()

        self.assertEqual(record_id, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["error_category"], "graphics")
        self.assertEqual(records[0]["gpu_vendor"], "amd")

    def test_rejects_arbitrary_category(self):
        data = self.valid_record()
        data["error_category"] = "arbitrary raw log content"

        with self.assertRaises(ValueError):
            self.store.add_record(**data)

    def test_rejects_path_in_version(self):
        data = self.valid_record()
        data["wine_version"] = "/home/user/private/file"

        with self.assertRaises(ValueError):
            self.store.add_record(**data)

    def test_rejects_raw_log_argument(self):
        data = self.valid_record()
        data["raw_log"] = "private diagnostic log"

        with self.assertRaises(TypeError):
            self.store.add_record(**data)

    def test_rejects_invalid_limit(self):
        with self.assertRaises(ValueError):
            self.store.list_records(limit=1001)

    def test_rejects_unknown_schema_version(self):
        other_db = Path(self.temp.name) / "future.sqlite3"
        connection = sqlite3.connect(other_db)
        connection.execute("PRAGMA user_version = 999")
        connection.close()

        with self.assertRaises(KnowledgeStoreError):
            KnowledgeStore(other_db)


    def test_rejects_caller_supplied_proven_status_at_store_boundary(self):
        record = self.valid_record()
        record["evidence_status"] = "PROVEN"

        with self.assertRaisesRegex(ValueError, "trusted evidence provenance"):
            self.store.add_record(**record)

        with self.assertRaisesRegex(ValueError, "trusted evidence provenance"):
            self.store.add_records_atomic([record])

        self.assertEqual(self.store.list_records(), [])


    def test_atomic_rejection_leaves_no_partial_insert(self):
        # Documented property (security/proven-write-barrier): the batch is
        # validated in full BEFORE the write transaction opens, so a PROVEN
        # record anywhere in a batch must reject the whole batch with zero
        # rows inserted — including rows that individually looked valid.
        seed = self.store.add_record(**self.valid_record())
        self.assertTrue(seed)

        good = self.valid_record()
        good["wine_version"] = "11.20"
        bad = self.valid_record()
        bad["evidence_status"] = "PROVEN"

        before = len(self.store.list_records())
        with self.assertRaisesRegex(ValueError, "trusted evidence provenance"):
            self.store.add_records_atomic([good, bad])
        after = len(self.store.list_records())

        self.assertEqual(before, after, "PROVEN rejection must not leave a partial insert")
        self.assertEqual(after, 1)  # only the seed row exists

    def test_proven_rejection_happens_before_transaction(self):
        # Reviewer requirement (unified plan §3, package B): the PROVEN
        # check must happen BEFORE any write transaction opens. If the
        # validation is ever moved inside the transaction loop, this
        # test fails because _connect() — the seam every write
        # transaction must pass — would be reached during rejection.
        bad = self.valid_record()
        bad["evidence_status"] = "PROVEN"

        def forbidden_connect(*args, **kwargs):
            raise AssertionError(
                "a write transaction was opened during PROVEN rejection; "
                "validation must precede any transaction (§2 batch atomicity)"
            )

        with mock.patch.object(self.store, "_connect", side_effect=forbidden_connect):
            with self.assertRaisesRegex(ValueError, "trusted evidence provenance"):
                self.store.add_records_atomic([bad])
        # A single valid record must still be insertable through the same
        # seam afterwards (the patch is scoped to the rejection only).

    def test_rejection_is_atomic_no_partial_insert(self):
        # Reviewer test (unified plan §3): a batch with valid records
        # before AND after the PROVEN one must leave the store byte-for-
        # byte unchanged — zero partial inserts in either direction.
        good1 = self.valid_record()
        good1["wine_version"] = "11.0"
        good2 = self.valid_record()
        good2["wine_version"] = "11.1"
        bad = self.valid_record()
        bad["evidence_status"] = "PROVEN"

        before = self.store.list_records()
        with self.assertRaisesRegex(ValueError, "trusted evidence provenance"):
            self.store.add_records_atomic([good1, bad, good2])
        self.assertEqual(self.store.list_records(), before)


if __name__ == "__main__":
    unittest.main()
