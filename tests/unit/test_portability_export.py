import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from wine_ai.knowledge.store import KnowledgeStore, SCHEMA_VERSION
from wine_ai.portability import (
    FORMAT_ID,
    MAX_EXPORT_RECORDS,
    PACKAGE_VERSION,
    PortabilityError,
    _year_month,
    export_records,
    export_to_file,
    write_package,
)


def _new_store(root: Path, name: str = "k.sqlite3") -> KnowledgeStore:
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


def _add_legacy_proven(store):
    """Insert a pre-gate historical row to exercise read/export compatibility."""
    with sqlite3.connect(str(store.db_path)) as connection:
        connection.execute("""
            INSERT INTO diagnostic_records (
                created_at, wine_version, distro, error_category,
                evidence_status, confidence, gpu_vendor, error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "2026-11-12T00:00:00+00:00", "11.19", "Manjaro",
            "memory", "PROVEN", "high", "amd", "STATUS_DLL_NOT_FOUND",
        ))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.store = _new_store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_store_produces_empty_package(self):
        package = export_records(self.store)
        self.assertEqual(package["format"], FORMAT_ID)
        self.assertEqual(package["package_version"], PACKAGE_VERSION)
        self.assertEqual(package["schema_version"], SCHEMA_VERSION)
        self.assertEqual(package["record_count"], 0)
        self.assertEqual(package["records"], [])
        self.assertFalse(package["includes_prefix_id"])
        self.assertTrue(package["includes_time"])

    def test_round_trip_all_fields_preserved(self):
        _add(self.store, error_code="0xC0000005", gpu_vendor="intel")
        _add_legacy_proven(self.store)
        package = export_records(self.store)
        self.assertEqual(package["record_count"], 2)
        codes = sorted(r.get("error_code", "") for r in package["records"])
        self.assertEqual(codes, ["0xC0000005", "STATUS_DLL_NOT_FOUND"])
        cats = sorted(r["error_category"] for r in package["records"])
        self.assertEqual(cats, ["graphics", "memory"])

    def test_default_excludes_prefix_id(self):
        _add(self.store, prefix_id="abc")
        package = export_records(self.store)
        self.assertFalse(package["includes_prefix_id"])
        for record in package["records"]:
            self.assertNotIn("prefix_id", record)

    def test_opt_in_includes_prefix_id(self):
        _add(self.store, prefix_id="abc")
        package = export_records(self.store, include_prefix_id=True)
        self.assertTrue(package["includes_prefix_id"])
        self.assertEqual(package["records"][0]["prefix_id"], "abc")

    def test_opt_in_without_prefix_id_omits_field(self):
        _add(self.store)
        package = export_records(self.store, include_prefix_id=True)
        self.assertNotIn("prefix_id", package["records"][0])

    def test_prefix_hash_is_never_exported(self):
        # Even opt-in for prefix_id must never carry prefix_hash: it is
        # a local pseudonym of a local path and must not leave the
        # machine (audit finding: "must not be exported in share
        # packages").
        _add(self.store, prefix_id="abc", prefix_hash="a" * 32)
        default = export_records(self.store)
        self.assertNotIn("prefix_hash", default["records"][0])
        opt_in = export_records(self.store, include_prefix_id=True)
        self.assertNotIn("prefix_hash", opt_in["records"][0])
        self.assertIn("prefix_id", opt_in["records"][0])

    def test_no_full_timestamp(self):
        _add(self.store)
        package = export_records(self.store)
        record = package["records"][0]
        self.assertNotIn("created_at", record)
        self.assertIn("created_at_ym", record)
        ym = record["created_at_ym"]
        self.assertEqual(len(ym), 7)
        self.assertEqual(ym[4], "-")

    def test_include_time_false_omits_timestamp(self):
        _add(self.store)
        package = export_records(self.store, include_time=False)
        self.assertFalse(package["includes_time"])
        self.assertNotIn("created_at_ym", package["records"][0])

    def test_max_records_cap(self):
        for _ in range(5):
            _add(self.store)
        package = export_records(self.store, max_records=2)
        self.assertEqual(package["record_count"], 2)

    def test_invalid_max_records_rejected(self):
        with self.assertRaises(PortabilityError):
            export_records(self.store, max_records=0)
        with self.assertRaises(PortabilityError):
            export_records(self.store, max_records=MAX_EXPORT_RECORDS + 1)
        with self.assertRaises(PortabilityError):
            export_records(self.store, max_records=True)  # type: ignore[arg-type]

    def test_invalid_flags_rejected(self):
        with self.assertRaises(PortabilityError):
            export_records(self.store, include_prefix_id="yes")  # type: ignore[arg-type]
        with self.assertRaises(PortabilityError):
            export_records(self.store, include_time=1)  # type: ignore[arg-type]

    def test_write_package_creates_valid_json_file(self):
        _add(self.store)
        package = export_records(self.store)
        out = self.root / "pkg.json"
        write_package(package, out)
        self.assertTrue(out.is_file())
        reloaded = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["format"], FORMAT_ID)
        self.assertEqual(reloaded["record_count"], 1)

    def test_export_to_file_convenience(self):
        _add(self.store)
        out = self.root / "pkg.json"
        export_to_file(self.store, out)
        reloaded = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["record_count"], 1)

    # -- export re-validation gate -------------------------------------------

    def test_export_blocked_when_tampered_row_detected(self):
        # A row tampered with through a raw SQLite connection (the only
        # field the SQL CHECK constraints cannot cover is created_at)
        # must block the export instead of leaving the store.
        _add(self.store)
        connection = sqlite3.connect(str(self.store.db_path))
        try:
            connection.execute(
                "UPDATE diagnostic_records "
                "SET created_at = 'garbage-timestamp' "
                "WHERE id = (SELECT MIN(id) FROM diagnostic_records)"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(PortabilityError):
            export_records(self.store)

    # -- write safety ---------------------------------------------------------

    def test_write_package_rejects_symlink_target(self):
        victim = self.root / "victim.txt"
        victim.write_text("innocent", encoding="utf-8")
        link = self.root / "pkg.json"
        os.symlink(victim, link)

        _add(self.store)
        package = export_records(self.store)
        with self.assertRaises(PortabilityError):
            write_package(package, link)

        self.assertEqual(victim.read_text(encoding="utf-8"), "innocent")
        self.assertTrue(link.is_symlink())

    def test_write_package_replaces_existing_file_atomically(self):
        _add(self.store)
        out = self.root / "pkg.json"
        out.write_text("old content", encoding="utf-8")
        os.chmod(out, 0o644)

        package = export_records(self.store)
        write_package(package, out)

        reloaded = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["record_count"], 1)
        # Exported packages carry diagnostic metadata: owner-only.
        mode = stat.S_IMODE(out.lstat().st_mode) & 0o777
        self.assertEqual(mode, 0o600)
        # No temp files left behind.
        leftovers = [
            p.name for p in self.root.iterdir()
            if p.name.startswith(".wine-ai-export-")
        ]
        self.assertEqual(leftovers, [])

    def test_write_package_rejects_invalid_package_before_write(self):
        out = self.root / "pkg.json"
        with self.assertRaises(PortabilityError):
            write_package({"format": "bogus"}, out)
        self.assertFalse(out.exists())

    # -- timestamp reduction -----------------------------------------------------

    def test_year_month_rejects_malformed_sources(self):
        self.assertEqual(_year_month("2026-11-11T10:43:19+00:00"), "2026-11")
        # Reduced-precision values do not parse as full ISO datetimes,
        # so they yield the documented "unknown" placeholder.
        self.assertEqual(_year_month("2026-11"), "unknown")
        self.assertEqual(_year_month("not-a-date"), "unknown")
        self.assertEqual(_year_month("2026-13-01T00:00:00"), "unknown")
        self.assertEqual(_year_month(""), "unknown")
        self.assertEqual(_year_month(None), "unknown")
        self.assertEqual(_year_month(202611), "unknown")


if __name__ == "__main__":
    unittest.main()
