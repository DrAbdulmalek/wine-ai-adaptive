"""Tests for the read-only ``wine-ai diagnose`` subcommand.

The diagnose command must stay strictly observational: it reads a log,
prints structured findings, and stores nothing.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
