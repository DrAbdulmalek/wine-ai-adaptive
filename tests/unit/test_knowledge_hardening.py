import sqlite3
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.store import KnowledgeStore


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


class KnowledgeHardeningTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.store = KnowledgeStore(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_batch_returns_empty_list(self):
        self.assertEqual(self.store.add_records_atomic([]), [])
        self.assertEqual(self.store.list_records(), [])

    def test_valid_batch_persists_all_records(self):
        ids = self.store.add_records_atomic([
            valid_record(),
            valid_record(error_category="memory"),
        ])

        self.assertEqual(len(ids), 2)
        self.assertEqual(len(self.store.list_records()), 2)

    def test_invalid_second_record_rejects_entire_batch(self):
        with self.assertRaises(ValueError):
            self.store.add_records_atomic([
                valid_record(),
                valid_record(error_category="invalid"),
            ])

        self.assertEqual(self.store.list_records(), [])

    def test_database_failure_rolls_back_entire_batch(self):
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute("""
                CREATE TRIGGER reject_memory_record
                BEFORE INSERT ON diagnostic_records
                WHEN NEW.error_category = 'memory'
                BEGIN
                    SELECT RAISE(ABORT, 'forced test failure');
                END
            """)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_records_atomic([
                valid_record(error_category="graphics"),
                valid_record(error_category="memory"),
            ])

        self.assertEqual(self.store.list_records(), [])

    def test_batch_over_limit_is_rejected(self):
        records = [valid_record() for _ in range(101)]

        with self.assertRaises(ValueError):
            self.store.add_records_atomic(records)

        self.assertEqual(self.store.list_records(), [])

    def test_extra_fields_are_rejected(self):
        record = valid_record(raw_log="must not be stored")

        with self.assertRaises(ValueError):
            self.store.add_records_atomic([record])

        self.assertEqual(self.store.list_records(), [])

    def test_non_dictionary_record_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_records_atomic([valid_record(), "invalid"])

        self.assertEqual(self.store.list_records(), [])

    def test_non_list_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_records_atomic(tuple([valid_record()]))

        self.assertEqual(self.store.list_records(), [])

    def test_add_record_remains_compatible(self):
        record_id = self.store.add_record(**valid_record())

        self.assertIsInstance(record_id, int)
        self.assertEqual(len(self.store.list_records()), 1)


if __name__ == "__main__":
    unittest.main()
