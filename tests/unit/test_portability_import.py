import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wine_ai import portability as portability_module
from wine_ai.knowledge.store import (
    KnowledgeStore,
    KnowledgeStoreError,
    SCHEMA_VERSION,
)
from wine_ai.portability import (
    FORMAT_ID,
    MAX_IMPORT_RECORDS,
    PACKAGE_VERSION,
    PortabilityError,
    export_records,
    export_to_file,
    import_package,
    import_package_file,
    import_package_lenient,
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
        _add(source, error_category="memory", evidence_status="PARTIALLY_PROVEN",
             confidence="high", error_code="STATUS_DLL_NOT_FOUND")
        package = export_records(source)

        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)

        self.assertEqual(len(ids), 2)
        self.assertEqual(_signature(target.list_records()), _signature(source.list_records()))

    def test_rejects_caller_supplied_proven_status(self):
        package = _valid_package([_valid_record(evidence_status="PROVEN")])
        target = _new_store(self.root, "target-proven.sqlite3")

        with self.assertRaisesRegex(PortabilityError, "trusted evidence provenance"):
            import_package(target, package)

        self.assertEqual(target.list_records(), [])

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

    # -- store contract ------------------------------------------------------

    def test_prepare_record_contract(self):
        # portability relies on KnowledgeStore._prepare_record: pin the
        # contract (staticmethod, accepts a dict, returns the 11-value
        # insert tuple, raises ValueError on any violation) so a silent
        # upstream change cannot break import validation.
        attr = inspect.getattr_static(KnowledgeStore, "_prepare_record")
        self.assertIsInstance(attr, staticmethod)

        prepared = KnowledgeStore._prepare_record(_valid_record())
        self.assertIsInstance(prepared, tuple)
        self.assertEqual(len(prepared), 11)

        with self.assertRaises(ValueError):
            KnowledgeStore._prepare_record("not-a-dict")
        bad = _valid_record()
        bad["raw_log"] = "x"
        with self.assertRaises(ValueError):
            KnowledgeStore._prepare_record(bad)

    # -- record-shape edge cases ----------------------------------------------

    def test_rejects_record_not_dict(self):
        package = _valid_package(["just a string"])
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)
        self.assertEqual(target.list_records(), [])

    def test_accepts_missing_prefix_id_when_opted_in(self):
        # includes_prefix_id=True means the field MAY appear; records
        # without it are still valid (the stored column stays NULL).
        record = _valid_record()
        package = _valid_package([record], includes_prefix_id=True)
        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)
        self.assertEqual(len(ids), 1)
        self.assertIsNone(target.list_records()[0]["prefix_id"])

    def test_rejects_invalid_created_at_ym(self):
        for bad in ("2026-13", "2026-1", "not-a-date", 202611, None):
            with self.subTest(value=bad):
                record = _valid_record()
                record["created_at_ym"] = bad
                package = _valid_package([record])
                target = _new_store(self.root, "target.sqlite3")
                with self.assertRaises(PortabilityError):
                    import_package(target, package)
                self.assertEqual(target.list_records(), [])

    def test_accepts_created_at_ym_known_and_unknown(self):
        records = [_valid_record(), _valid_record()]
        records[0]["created_at_ym"] = "2026-11"
        records[1]["created_at_ym"] = "unknown"
        package = _valid_package(records)
        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)
        self.assertEqual(len(ids), 2)

    # -- header strictness -----------------------------------------------------

    def test_rejects_extra_header_key(self):
        package = _valid_package([_valid_record()])
        package["extra_field"] = "smuggled"
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_missing_header_key(self):
        package = _valid_package([_valid_record()])
        del package["includes_time"]
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_non_bool_includes_time(self):
        package = _valid_package([_valid_record()])
        package["includes_time"] = "yes"
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_bool_package_version(self):
        # bool is a subclass of int; True must NOT pass as version 1.
        package = _valid_package([_valid_record()])
        package["package_version"] = True
        target = _new_store(self.root, "target.sqlite3")
        with self.assertRaises(PortabilityError):
            import_package(target, package)

    def test_rejects_invalid_exported_at(self):
        for bad in ("2026-13", "2026-1", "2026-11-15", 202611, None):
            with self.subTest(value=bad):
                package = _valid_package([_valid_record()])
                package["exported_at"] = bad
                target = _new_store(self.root, "target.sqlite3")
                with self.assertRaises(PortabilityError):
                    import_package(target, package)

    # -- file-level edge cases --------------------------------------------------

    def test_read_package_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            read_package(self.root / "does-not-exist.json")

    def test_read_package_rejects_invalid_utf8(self):
        bad = self.root / "bad-utf8.json"
        bad.write_bytes(b"{\xff\xfe\x00}not utf8")
        with self.assertRaises(PortabilityError):
            read_package(bad)

    def test_read_package_rejects_symlink(self):
        real = self.root / "real.json"
        real.write_text("{}", encoding="utf-8")
        link = self.root / "link.json"
        os.symlink(real, link)
        with self.assertRaises(PortabilityError):
            read_package(link)

    def test_read_package_rejects_oversized_file(self):
        original_cap = portability_module.MAX_PACKAGE_BYTES
        portability_module.MAX_PACKAGE_BYTES = 64
        try:
            big = self.root / "big.json"
            big.write_text("{" + " " * 200 + "}", encoding="utf-8")
            with self.assertRaises(PortabilityError):
                read_package(big)
        finally:
            portability_module.MAX_PACKAGE_BYTES = original_cap

    # -- batch boundaries --------------------------------------------------------

    def test_accepts_exactly_max_import_records(self):
        records = [_valid_record() for _ in range(MAX_IMPORT_RECORDS)]
        package = _valid_package(records)
        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)
        self.assertEqual(len(ids), MAX_IMPORT_RECORDS)
        self.assertEqual(
            len(target.list_records(limit=MAX_IMPORT_RECORDS)),
            MAX_IMPORT_RECORDS,
        )

    def test_batch_boundaries_are_crossed_successfully(self):
        # 250 records = 3 chunks (100 + 100 + 50) across the store's
        # MAX_BATCH_SIZE boundary.
        records = [_valid_record() for _ in range(250)]
        package = _valid_package(records)
        target = _new_store(self.root, "target.sqlite3")
        ids = import_package(target, package)
        self.assertEqual(len(ids), 250)

    def test_later_batch_failure_keeps_earlier_batches(self):
        # Documented residual risk: insertion is per-batch atomic, not
        # per-package. A failure in a later batch keeps earlier batches;
        # this test pins that honest behavior.
        target = _new_store(self.root, "target.sqlite3")
        calls = {"count": 0}
        original = target.add_records_atomic

        def flaky_add(records):
            calls["count"] += 1
            if calls["count"] == 2:
                raise KnowledgeStoreError("injected later-batch failure")
            return original(records)

        target.add_records_atomic = flaky_add

        records = [_valid_record() for _ in range(150)]
        package = _valid_package(records)
        with self.assertRaises(KnowledgeStoreError):
            import_package(target, package)

        self.assertEqual(calls["count"], 2)
        stored = target.list_records(limit=1000)
        self.assertEqual(len(stored), 100)

    def test_export_preserves_historical_proven_rows(self):
        # Round-trip decision (2026-11-13): historical PROVEN rows are
        # exported exactly as stored — no downgrade on export, no data
        # loss. The fixture simulates a pre-barrier row by writing the
        # status directly (the store write barrier now forbids creating
        # one through the API).
        source = _new_store(self.root, "source.sqlite3")
        row_id = _add(source)
        connection = sqlite3.connect(source.db_path)
        try:
            connection.execute(
                "UPDATE diagnostic_records SET evidence_status = 'PROVEN' WHERE id = ?",
                (row_id,),
            )
            connection.commit()
        finally:
            connection.close()

        package = export_records(source)
        statuses = [r["evidence_status"] for r in package["records"]]
        self.assertEqual(statuses, ["PROVEN"])

    def test_lenient_import_downgrades_proven_and_preserves_claim(self):
        # Import decision (2026-11-13): lenient import downgrades PROVEN
        # to UNPROVEN and preserves the original claim in the report,
        # tied to the stored row via index and inserted id.
        target = _new_store(self.root, "target.sqlite3")
        package = _valid_package([
            _valid_record(evidence_status="UNPROVEN", confidence="low"),
            _valid_record(evidence_status="PROVEN", confidence="high"),
            _valid_record(evidence_status="PARTIALLY_PROVEN", confidence="medium"),
        ])

        report = import_package_lenient(target, package)

        self.assertEqual(report["record_count"], 3)
        self.assertEqual(len(report["inserted_ids"]), 3)
        self.assertEqual(len(report["downgraded"]), 1)
        entry = report["downgraded"][0]
        self.assertEqual(entry["index"], 1)
        self.assertEqual(entry["original_evidence_status"], "PROVEN")
        self.assertEqual(entry["final_evidence_status"], "UNPROVEN")
        self.assertEqual(entry["inserted_id"], report["inserted_ids"][1])

        stored = target.list_records(limit=10)
        # list_records is newest-first; compare as a multiset.
        self.assertEqual(
            sorted(r["evidence_status"] for r in stored),
            sorted(["UNPROVEN", "UNPROVEN", "PARTIALLY_PROVEN"]),
        )
        # no PROVEN row reached the store
        self.assertTrue(all(r["evidence_status"] != "PROVEN" for r in stored))

    def test_lenient_import_without_proven_is_unchanged(self):
        target = _new_store(self.root, "target.sqlite3")
        package = _valid_package([
            _valid_record(evidence_status="UNPROVEN"),
            _valid_record(evidence_status="BLOCKED", confidence="low"),
        ])

        report = import_package_lenient(target, package)

        self.assertEqual(report["downgraded"], [])
        self.assertEqual(report["record_count"], 2)
        self.assertEqual(len(target.list_records(limit=10)), 2)

    def test_strict_import_still_rejects_proven_package(self):
        # The strict entry point keeps its all-or-nothing rejection.
        target = _new_store(self.root, "target.sqlite3")
        package = _valid_package([
            _valid_record(evidence_status="UNPROVEN"),
            _valid_record(evidence_status="PROVEN"),
        ])
        with self.assertRaises(PortabilityError):
            import_package(target, package)
        self.assertEqual(target.list_records(), [])


if __name__ == "__main__":
    unittest.main()
