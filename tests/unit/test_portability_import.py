import json
import os
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.store import KnowledgeStore, SCHEMA_VERSION
from wine_ai.portability import (
    FORMAT_ID,
    MAX_IMPORT_RECORDS,
    PACKAGE_VERSION,
    PortabilityError,
    export_records,
    export_to_file,
    import_package,
    import_package_file,
    read_package,
    validate_package,
)


def _new_store(root: Path, name: str) -> KnowledgeStore:
    data = root / "data"
    data.mkdir(exist_ok=True, mode=0o700)
    os.chmod(data, 0o700)
    return KnowledgeStore(data / name)


def _add(store, **overrides):
    base = dict(
        wine_version="11.19",
        distro="Manjaro",
        error_category="graphics",
        evidence_status="PARTIALLY_PROVEN",
        confidence="low",
        gpu_vendor="amd",
    )
    base.update(overrides)
    return store.add_record(**base)


def _valid_package(records, *, includes_prefix_id=False, count=None):
    return {
        "format": FORMAT_ID,
        "package_version": PACKAGE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": "2026-11",
        "record_count": len(records) if count is None else count,
        "includes_prefix_id": includes_prefix_id,
        "includes_time": True,
        "records": records,
    }


def _valid_record(**overrides):
    base = {
        "wine_version": "11.19",
        "distro": "Manjaro",
        "error_category": "graphics",
        "evidence_status": "PARTIALLY_PROVEN",
        "confidence": "low",
        "gpu_vendor": "amd",
    }
    base.update(overrides)
    return base


def _signature(records):
    return sorted(
        (
            r["wine_version"],
            r["distro"],
            r["error_category"],
            r["evidence_status"],
            r["confidence"],
            r["gpu_vendor"],
            r.get("error_code"),
        )
        for r in records
    )


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_via_dict(self):
        source = _new_store(self.root, "source.sqlite3")
        _add(source, error_code="0xC0000005")
        _add(source, error_category="memory", evidence_status="PROVEN",
             confidence="high", error_code="STATUS_DLL_NOT_FOUND")
        package = export_records(source)

        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)

        self.assertEqual(len(ids), 2)
        self.assertEqual(_signature(target.list_records()), _signature(source.list_records()))

    def test_round_trip_via_file(self):
        source = _new_store(self.root, "source.sqlite3")
        _add(source, error_code="0xC0000005")
        path = self.root / "pkg.json"
        export_to_file(source, path)

        target = _new_store(self.root, "target.sqlite3")
        ids = import_package_file(target, path)

        self.assertEqual(len(ids), 1)
        self.assertEqual(target.list_records()[0]["error_code"], "0xC0000005")

    def test_round_trip_with_prefix_id(self):
        source = _new_store(self.root, "source.sqlite3")
        _add(source, error_code="0xC0000005", prefix_id="abc123")
        package = export_records(source, include_prefix_id=True)
        self.assertEqual(package["records"][0]["prefix_id"], "abc123")

        target = _new_store(self.root, "target.sqlite3")
        import_package(target, package)
        record = target.list_records()[0]
        self.assertEqual(record["prefix_id"], "abc123")
        self.assertEqual(record["error_code"], "0xC0000005")

    def test_rejects_wrong_format(self):
        package = _valid_package([_valid_record()])
        package["format"] = "something-else"
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)
        self.assertEqual(target.list_records(), [])

    def test_rejects_unsupported_package_version(self):
        package = _valid_package([_valid_record()])
        package["package_version"] = 999
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_schema_version_mismatch(self):
        package = _valid_package([_valid_record()])
        package["schema_version"] = SCHEMA_VERSION + 1
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_record_count_mismatch(self):
        package = _valid_package([_valid_record()], count=2)
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_unknown_keys(self):
        bad = _valid_record()
        bad["raw_log"] = "private"
        package = _valid_package([bad])
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)
        self.assertEqual(target.list_records(), [])

    def test_rejects_prefix_id_when_not_included(self):
        bad = _valid_record()
        bad["prefix_id"] = "leak"
        package = _valid_package([bad], includes_prefix_id=False)
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_accepts_prefix_id_when_included(self):
        ok = _valid_record()
        ok["prefix_id"] = "abc"
        package = _valid_package([ok], includes_prefix_id=True)
        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)
        self.assertEqual(len(ids), 1)
        self.assertEqual(target.list_records()[0]["prefix_id"], "abc")

    def test_ignores_created_at_ym_field(self):
        record = _valid_record()
        record["created_at_ym"] = "1999-01"  # legacy value
        package = _valid_package([record])
        target = _new_store(self.root, "target.sqlite3")
        import_package(target, package)
        stored = target.list_records()[0]
        self.assertNotEqual(stored["created_at"][:4], "1999")

    def test_validation_atomicity_invalid_record_rejects_whole_package(self):
        good = _valid_record()
        bad = _valid_record()
        bad["error_category"] = "INVALID_CATEGORY"
        package = _valid_package([good, bad])
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)
        self.assertEqual(target.list_records(), [])

    def test_empty_package_returns_empty_list(self):
        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, _valid_package([]))
        self.assertEqual(ids, [])
        self.assertEqual(target.list_records(), [])

    def test_too_many_records_rejected(self):
        records = [_valid_record() for _ in range(MAX_IMPORT_RECORDS + 1)]
        package = _valid_package(records)
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_validate_package_public_helper(self):
        package = _valid_package([_valid_record()])
        self.assertIs(validate_package(package), package)

    def test_read_package_invalid_json(self):
        bad = self.root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(PortabilityError):
            read_package(bad)

    def test_import_does_not_leak_extra_fields_to_store(self):
        good = _valid_record()
        package = _valid_package([good])
        target = _new_store(self.root, "target.sqlite3")
        import_package(target, package)
        stored = target.list_records()[0]
        self.assertNotIn("raw_log", stored)
        self.assertNotIn("created_at_ym", stored)


if __name__ == "__main__":
    unittest.main()
