"""Tests for the read-only query layer of KnowledgeStore."""

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


class QueryLayerTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "query.sqlite3"
        self.store = KnowledgeStore(self.db)
        self.store.add_records_atomic([
            valid_record(error_category="graphics"),
            valid_record(error_category="memory", gpu_vendor="nvidia"),
            valid_record(
                error_category="memory",
                gpu_vendor="amd",
                error_code="0xC0000005",
                prefix_id="game-1234",
            ),
            valid_record(
                error_category="missing_module",
                error_code="STATUS_DLL_NOT_FOUND",
            ),
        ])
        # Simulate a legacy row written before the public API provenance gate.
        with sqlite3.connect(self.db) as connection:
            connection.execute("""
                INSERT INTO diagnostic_records (
                    created_at, wine_version, distro, error_category,
                    evidence_status, confidence, gpu_vendor, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "2026-11-12T00:00:00+00:00", "11.19", "Manjaro",
                "unknown", "PROVEN", "high", "unknown",
                "STATUS_DLL_NOT_FOUND",
            ))

    def tearDown(self):
        self.temp.cleanup()

    def test_find_by_error_category(self):
        records = self.store.find_by_error_category("memory")
        self.assertEqual(len(records), 2)
        self.assertTrue(
            all(r["error_category"] == "memory" for r in records)
        )

    def test_find_by_error_category_rejects_unknown(self):
        with self.assertRaises(ValueError):
            self.store.find_by_error_category("arbitrary raw category")

    def test_find_by_gpu_vendor(self):
        records = self.store.find_by_gpu_vendor("nvidia")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["error_category"], "memory")

    def test_find_by_gpu_vendor_rejects_unknown(self):
        with self.assertRaises(ValueError):
            self.store.find_by_gpu_vendor("S3 Graphics")

    def test_find_by_evidence_status(self):
        records = self.store.find_by_evidence_status("PROVEN")
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["error_code"], "STATUS_DLL_NOT_FOUND"
        )

    def test_find_by_error_code_is_normalized(self):
        records = self.store.find_by_error_code("0xc0000005")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["gpu_vendor"], "amd")
        self.assertEqual(records[0]["prefix_id"], "game-1234")

        records = self.store.find_by_error_code("status_dll_not_found")
        self.assertEqual(len(records), 1)

    def test_find_by_error_code_rejects_invalid(self):
        with self.assertRaises(ValueError):
            self.store.find_by_error_code("DROP TABLE diagnostic_records")
        with self.assertRaises(ValueError):
            self.store.find_by_error_code(42)

    def test_category_counts(self):
        counts = self.store.category_counts()
        self.assertEqual(counts["memory"], 2)
        self.assertEqual(counts["graphics"], 1)
        self.assertEqual(counts["missing_module"], 1)
        self.assertNotIn("startup", counts)

    def test_correlate_error_codes(self):
        self.store.add_record(
            **valid_record(error_code="0xC0000005", gpu_vendor="intel")
        )
        correlated = self.store.correlate_error_codes()
        self.assertEqual(correlated[0]["error_code"], "0xC0000005")
        self.assertEqual(correlated[0]["count"], 2)

    def test_limits_are_validated(self):
        with self.assertRaises(ValueError):
            self.store.find_by_error_category("memory", limit=0)
        with self.assertRaises(ValueError):
            self.store.find_by_gpu_vendor("amd", limit=1001)
        with self.assertRaises(ValueError):
            self.store.correlate_error_codes(limit=-1)
        with self.assertRaises(ValueError):
            self.store.list_records(limit=True)
        limited = self.store.find_by_error_category("memory", limit=1)
        self.assertEqual(len(limited), 1)

    def test_query_results_carry_all_structured_fields(self):
        record = self.store.find_by_error_code("0xC0000005")[0]
        expected_keys = {
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
        }
        self.assertEqual(set(record), expected_keys)


if __name__ == "__main__":
    unittest.main()
