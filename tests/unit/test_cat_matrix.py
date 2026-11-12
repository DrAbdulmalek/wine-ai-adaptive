"""Matrix-check contract tests (unified plan §5.2, item 12).

These pin scripts/check_cat_matrix.py: the repo's committed matrix
(all-null) passes; a PASS without an existing log file fails — that is
the false-claim the check exists to prevent.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_cat_matrix.py"

sys.path.insert(0, str(SCRIPT.parent))
import check_cat_matrix  # noqa: E402


def _valid_matrix(tmpdir: Path, *, stage_entry):
    return {
        "schema_version": "1",
        "status": "UNPROVEN",
        "applications": {
            "trados": {
                "versions": ["2022"],
                "wine_versions": ["9.0"],
                "prefix_arch": ["win64"],
                "stages": {"install": stage_entry},
            }
        },
        "notes": "test fixture",
    }


class CatMatrixCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, payload) -> Path:
        path = self.root / "matrix.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_repo_committed_matrix_passes(self):
        # The shipped matrix is all-null: everything UNPROVEN, no false
        # claims. This pins the default state the plan requires.
        problems = check_cat_matrix.check_matrix(
            REPO_ROOT / "docs" / "cat" / "compatibility-matrix.json"
        )
        self.assertEqual(problems, [])

    def test_all_null_stages_pass(self):
        path = self._write(_valid_matrix(self.root, stage_entry=None))
        self.assertEqual(check_cat_matrix.check_matrix(path), [])

    def test_matrix_pass_requires_log_file(self):
        # Plan-named acceptance: a PASS whose log does not exist must
        # fail the check.
        path = self._write(_valid_matrix(self.root, stage_entry={
            "result": "PASS",
            "log": str(self.root / "nope" / "missing.log"),
            "date": "2026-11-13T00:00:00+00:00",
        }))
        problems = check_cat_matrix.check_matrix(path)
        self.assertTrue(problems)
        self.assertTrue(
            any("log file does not exist" in p for p in problems),
            f"expected a missing-log problem, got: {problems}",
        )

    def test_documented_stage_with_existing_log_passes(self):
        log = self.root / "evidence.log"
        log.write_text("sanitized evidence", encoding="utf-8")
        path = self._write(_valid_matrix(self.root, stage_entry={
            "result": "PASS",
            "log": str(log),
            "date": "2026-11-13T00:00:00+00:00",
        }))
        self.assertEqual(check_cat_matrix.check_matrix(path), [])

    def test_fail_and_blocked_results_also_require_log(self):
        log = self.root / "evidence2.log"
        log.write_text("sanitized failure evidence", encoding="utf-8")
        for result in ("FAIL", "BLOCKED"):
            with self.subTest(result=result):
                path = self._write(_valid_matrix(self.root, stage_entry={
                    "result": result,
                    "log": str(self.root / "absent.log"),
                    "date": "2026-11-13T00:00:00+00:00",
                }))
                problems = check_cat_matrix.check_matrix(path)
                self.assertTrue(
                    any("log file does not exist" in p for p in problems)
                )

    def test_status_may_not_leave_unproven_until_everything_documented(self):
        log = self.root / "evidence3.log"
        log.write_text("sanitized", encoding="utf-8")
        payload = _valid_matrix(self.root, stage_entry={
            "result": "PASS",
            "log": str(log),
            "date": "2026-11-13T00:00:00+00:00",
        })
        # Partial documentation: one stage filled, one still null.
        payload["applications"]["trados"]["stages"]["stability_1h"] = None
        payload["status"] = "PROVEN_BY_EVIDENCE"
        path = self._write(payload)
        problems = check_cat_matrix.check_matrix(path)
        self.assertTrue(
            any("status=" in p and "forbidden" in p for p in problems),
            f"partial documentation must not allow a status change: {problems}",
        )

    def test_status_may_change_when_every_stage_is_documented(self):
        log = self.root / "evidence4.log"
        log.write_text("sanitized", encoding="utf-8")
        payload = _valid_matrix(self.root, stage_entry={
            "result": "PASS",
            "log": str(log),
            "date": "2026-11-13T00:00:00+00:00",
        })
        payload["applications"]["trados"]["stages"]["stability_1h"] = {
            "result": "FAIL",
            "log": str(log),
            "date": "2026-11-13T01:00:00+00:00",
        }
        payload["status"] = "EVIDENCED_PARTIAL"
        path = self._write(payload)
        self.assertEqual(check_cat_matrix.check_matrix(path), [])

    def test_cli_exit_codes(self):
        import subprocess
        # repo matrix -> 0; broken matrix -> 1
        rc_ok = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True,
        )
        self.assertEqual(rc_ok.returncode, 0, rc_ok.stderr)

        bad = self.root / "bad.json"
        bad.write_text(json.dumps(_valid_matrix(self.root, stage_entry={
            "result": "PASS",
            "log": str(self.root / "ghost.log"),
            "date": "2026-11-13",
        })), encoding="utf-8")
        rc_bad = subprocess.run(
            [sys.executable, str(SCRIPT), str(bad)],
            capture_output=True, text=True,
        )
        self.assertEqual(rc_bad.returncode, 1)
        self.assertIn("log file does not exist", rc_bad.stderr)


if __name__ == "__main__":
    unittest.main()
