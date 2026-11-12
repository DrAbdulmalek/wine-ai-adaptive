"""Tests for the read-only ``wine-ai diagnose`` subcommand.

The diagnose command must stay strictly observational: it reads a log,
prints structured findings, and stores nothing.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from wine_ai.cli import main


class DiagnoseCommandTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "wine.log"

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_reports_structured_codes(self):
        self.log.write_text(
            "err: exception 0xC0000005 at 0x7fff\n"
            "err:module:import_dll Library foo.dll not found\n",
            encoding="utf-8",
        )
        code, out = self._run(
            ["diagnose", "--log", str(self.log), "--json"]
        )
        payload = json.loads(out)
        self.assertEqual(code, 0)
        codes = {item["code"] for item in payload["findings"]}
        self.assertIn("0xC0000005", codes)
        self.assertGreater(
            payload["log_summary"]["matches"]["missing_module"], 0
        )
        # No raw log lines in the JSON payload
        self.assertNotIn("import_dll Library foo.dll not found", out)

    def test_diagnose_does_not_reopen_log_after_inspection(self):
        self.log.write_text("err: exception 0xC0000005 at 0x7fff\n", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("second read forbidden")):
            code, out = self._run(["diagnose", "--log", str(self.log), "--json"])
        self.assertEqual(code, 0)
        self.assertIn("0xC0000005", json.loads(out)["findings"][0]["code"])

    def test_no_raw_log_text_in_human_output(self):
        self.log.write_text(
            "err: exception 0xC0000005 at 0x7fff\n",
            encoding="utf-8",
        )
        code, out = self._run(["diagnose", "--log", str(self.log)])
        self.assertEqual(code, 0)
        self.assertIn("0xC0000005", out)
        self.assertNotIn("at 0x7fff", out)

    def test_missing_log_file_reports_blocked_evidence(self):
        missing = Path(self.temp.name) / "absent.log"
        code, out = self._run(
            ["diagnose", "--log", str(missing), "--json"]
        )
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("read_error", payload["log_summary"])
        statuses = [item["status"] for item in payload["evidence"]]
        self.assertIn("BLOCKED", statuses)

    def test_oversized_log_is_refused(self):
        big = Path(self.temp.name) / "big.log"
        big.write_text("x" * (3 * 1024 * 1024), encoding="utf-8")
        code, out = self._run(["diagnose", "--log", str(big), "--json"])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        statuses = [item["status"] for item in payload["evidence"]]
        self.assertIn("BLOCKED", statuses)

    def test_directory_is_not_a_log(self):
        code, out = self._run(
            ["diagnose", "--log", str(self.temp.name), "--json"]
        )
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("read_error", payload["log_summary"])

    def test_nothing_is_stored(self):
        self.log.write_text(
            "err: exception 0xC0000005 at 0x7fff\n",
            encoding="utf-8",
        )
        before = sorted(p.name for p in Path(self.temp.name).iterdir())
        self._run(["diagnose", "--log", str(self.log), "--json"])
        after = sorted(p.name for p in Path(self.temp.name).iterdir())
        self.assertEqual(before, after)

    def test_diagnose_requires_log_argument(self):
        with self.assertRaises(SystemExit):
            self._run(["diagnose"])


class PrefixesCommandTests(unittest.TestCase):
    """The prefixes command must stay read-only discovery + evidence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        env_patcher = mock.patch.dict(os.environ, clear=False)
        self.env = env_patcher.start()
        self.env.pop("WINEPREFIX", None)
        self.env["HOME"] = str(self.home)
        self.addCleanup(env_patcher.stop)

    def _make_prefix(self, name: str, arch: str = "win64") -> Path:
        managed = (
            self.home / ".local" / "share"
            / "wine-ai-adaptive" / "prefixes"
        )
        managed.mkdir(parents=True, exist_ok=True)
        prefix = managed / name
        (prefix / "drive_c").mkdir(parents=True)
        (prefix / "system.reg").write_text(
            f"##arch - {arch}\n", encoding="ascii"
        )
        for reg in ("user.reg", "userdef.reg"):
            (prefix / reg).write_text("## WINE registry\n", encoding="ascii")
        return prefix

    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_prefixes_json_reports_discovered_prefix(self):
        self._make_prefix("trados", arch="win64")
        code, out = self._run(["prefixes", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        names = [item["name"] for item in payload["prefixes"]]
        self.assertIn("trados", names)
        info = next(
            item for item in payload["prefixes"]
            if item["name"] == "trados"
        )
        self.assertEqual(info["architecture"], "win64")
        self.assertFalse(info["protected"])
        statuses = [item["status"] for item in payload["evidence"]]
        self.assertIn("PROVEN", statuses)

    def test_prefixes_human_output_mentions_protection_note(self):
        self._make_prefix("memoq")
        code, out = self._run(["prefixes"])
        self.assertEqual(code, 0)
        self.assertIn("memoq", out)
        self.assertIn("read-only discovery", out)
        self.assertIn("not implemented by design", out)

    def test_prefixes_flags_default_prefix_as_protected(self):
        prefix = self.home / ".wine"
        (prefix / "drive_c").mkdir(parents=True)
        (prefix / "system.reg").write_text("##arch - win64\n", encoding="ascii")
        for reg in ("user.reg", "userdef.reg"):
            (prefix / reg).write_text("## WINE registry\n", encoding="ascii")
        code, out = self._run(["prefixes", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        info = next(
            item for item in payload["prefixes"]
            if item["name"] == ".wine"
        )
        self.assertTrue(info["protected"])

    def test_prefixes_empty_environment_still_succeeds(self):
        code, out = self._run(["prefixes", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["prefixes"], [])
        self.assertTrue(payload["evidence"])

    def test_prefixes_stores_nothing(self):
        before = sorted(p.name for p in self.home.iterdir())
        self._run(["prefixes", "--json"])
        after = sorted(p.name for p in self.home.iterdir())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
