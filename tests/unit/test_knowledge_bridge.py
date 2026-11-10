import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wine_ai.knowledge.store import KnowledgeStore, MAX_BATCH_SIZE
from wine_ai.knowledge.bridge import persist_findings


class KnowledgeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "bridge.sqlite3"
        self.store = KnowledgeStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_structured_finding(self):
        findings = [{
            "category": "graphics",
            "status": "PARTIALLY_PROVEN",
            "confidence": "low",
            "matched_patterns": ["private text must not be stored"],
        }]

        ids = persist_findings(
            self.store,
            findings,
            wine_version="11.19",
            distro="Manjaro",
            gpu_vendor="unknown",
        )

        records = self.store.list_records()

        self.assertEqual(ids, [1])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["error_category"], "graphics")
        self.assertEqual(records[0]["gpu_vendor"], "unknown")
        self.assertNotIn("matched_patterns", records[0])

    def test_unknown_category_is_normalized(self):
        ids = persist_findings(
            self.store,
            [{"category": "unrecognized-category"}],
            wine_version="11.19",
            distro="Manjaro",
        )

        record = self.store.list_records()[0]
        self.assertEqual(ids, [1])
        self.assertEqual(record["error_category"], "unknown")

    def test_extra_raw_log_field_is_ignored(self):
        finding = {
            "category": "memory",
            "raw_log": "PRIVATE RAW LOG CONTENT",
            "path": "/home/private/document",
        }

        persist_findings(
            self.store,
            [finding],
            wine_version="11.19",
            distro="Manjaro",
        )

        record = self.store.list_records()[0]
        self.assertNotIn("raw_log", record)
        self.assertNotIn("path", record)

    def test_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            persist_findings(
                self.store,
                [{"category": "graphics", "status": "UNKNOWN_STATUS"}],
                wine_version="11.19",
                distro="Manjaro",
            )

    def test_rejects_non_list_findings(self):
        with self.assertRaises(TypeError):
            persist_findings(
                self.store,
                "not a list",
                wine_version="11.19",
                distro="Manjaro",
            )

    def test_empty_findings_save_nothing(self):
        ids = persist_findings(
            self.store,
            [],
            wine_version="11.19",
            distro="Manjaro",
        )

        self.assertEqual(ids, [])
        self.assertEqual(self.store.list_records(), [])

    def test_rejects_batch_over_maximum_size(self):
        findings = [{} for _ in range(MAX_BATCH_SIZE + 1)]

        with self.assertRaises(ValueError):
            persist_findings(
                self.store,
                findings,
                wine_version="11.19",
                distro="Manjaro",
            )

        self.assertEqual(self.store.list_records(), [])

    def test_invalid_second_finding_persists_nothing(self):
        findings = [
            {"category": "graphics"},
            {"category": "memory", "status": "INVALID_STATUS"},
        ]

        with self.assertRaises(ValueError):
            persist_findings(
                self.store,
                findings,
                wine_version="11.19",
                distro="Manjaro",
            )

        self.assertEqual(self.store.list_records(), [])

    def test_rejects_non_string_finding_values(self):
        invalid_findings = [
            {"category": ["graphics"]},
            {"category": "graphics", "status": ["PARTIALLY_PROVEN"]},
            {"category": "graphics", "confidence": ["low"]},
        ]

        for finding in invalid_findings:
            with self.subTest(finding=finding):
                with self.assertRaises(ValueError):
                    persist_findings(
                        self.store,
                        [finding],
                        wine_version="11.19",
                        distro="Manjaro",
                    )

                self.assertEqual(self.store.list_records(), [])

    def test_rejects_non_string_gpu_vendor(self):
        with self.assertRaises(ValueError):
            persist_findings(
                self.store,
                [{"category": "graphics"}],
                wine_version="11.19",
                distro="Manjaro",
                gpu_vendor=["unknown"],
            )

        self.assertEqual(self.store.list_records(), [])

    def test_uses_atomic_batch_api_once(self):
        findings = [
            {"category": "graphics"},
            {"category": "memory"},
        ]

        with (
            patch.object(
                self.store,
                "add_records_atomic",
                wraps=self.store.add_records_atomic,
            ) as atomic_method,
            patch.object(
                self.store,
                "add_record",
                side_effect=AssertionError(
                    "Bridge must not use individual inserts"
                ),
            ),
        ):
            ids = persist_findings(
                self.store,
                findings,
                wine_version="11.19",
                distro="Manjaro",
            )

        self.assertEqual(ids, [1, 2])
        atomic_method.assert_called_once()

        submitted_records = atomic_method.call_args.args[0]
        self.assertEqual(len(submitted_records), 2)
        self.assertEqual(
            [record["error_category"] for record in submitted_records],
            ["graphics", "memory"],
        )

        self.assertEqual(len(self.store.list_records()), 2)




    def test_rejects_invalid_wine_version_without_persistence(self):
        with self.assertRaises(ValueError):
            persist_findings(
                self.store,
                [{"category": "graphics"}],
                wine_version=" 11.19",
                distro="Manjaro",
            )

        self.assertEqual(self.store.list_records(), [])

    def test_rejects_invalid_distro_without_persistence(self):
        with self.assertRaises(ValueError):
            persist_findings(
                self.store,
                [{"category": "graphics"}],
                wine_version="11.19",
                distro="Manjaro\nINVALID",
            )

        self.assertEqual(self.store.list_records(), [])

    def test_persists_maximum_valid_batch(self):
        findings = [
            {"category": "graphics"}
            for _ in range(MAX_BATCH_SIZE)
        ]

        ids = persist_findings(
            self.store,
            findings,
            wine_version="11.19",
            distro="Manjaro",
        )

        records = self.store.list_records(limit=1000)

        self.assertEqual(len(ids), MAX_BATCH_SIZE)
        self.assertEqual(len(records), MAX_BATCH_SIZE)
        self.assertEqual(ids, list(range(1, MAX_BATCH_SIZE + 1)))

    def test_sqlite_failure_rolls_back_bridge_batch(self):
        connection = sqlite3.connect(str(self.db))

        try:
            connection.execute("""
                CREATE TRIGGER reject_memory
                BEFORE INSERT ON diagnostic_records
                WHEN NEW.error_category = 'memory'
                BEGIN
                    SELECT RAISE(ABORT, 'blocked for test');
                END
            """)
            connection.commit()
        finally:
            connection.close()

        findings = [
            {"category": "graphics"},
            {"category": "memory"},
        ]

        with self.assertRaises(sqlite3.IntegrityError):
            persist_findings(
                self.store,
                findings,
                wine_version="11.19",
                distro="Manjaro",
            )

        self.assertEqual(self.store.list_records(), [])

if __name__ == "__main__":
    unittest.main()
