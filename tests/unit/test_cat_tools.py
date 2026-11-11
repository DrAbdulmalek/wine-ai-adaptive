"""Tests for the CAT tools diagnostic refinement layer.

Origin: external DeepSeek review session (2026-11-11, turn 6) recommending
CAT-specific hints and suggestion-only winetricks integration. Iron rules
verified here: standard library only, suggestions are never executed,
no raw log text leaves the layer, KnowledgeStore category vocabulary is
not extended, and unknown input degrades to honest "no refinement".
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

from wine_ai.cli import main as cli_main
from wine_ai.diagnostics import cat_tools
from wine_ai.diagnostics.codes import extract_error_codes


SAMPLE_LOG = (
    "err:module:import_dll Library mscoree.dll (which is needed by "
    "L\"C:\\Program Files\\Trados\\TradosStudio.exe\") not found\n"
    "Unhandled exception: 0x80131700 .NET Framework initialization error\n"
    "err:mscoree:LoaderLoop Failed to register assembly (fusion.dll)\n"
    "err:module:import_dll Library msvcp140.dll not found\n"
    "err:module:import_dll Library msvcr70.dll not found\n"
)

GENERIC_FINDINGS = extract_error_codes(
    "0x80131700 0xC0000135 0x8007007E"
)


class CatProfilesTests(unittest.TestCase):

    def test_profiles_exist_with_documented_metadata(self):
        self.assertEqual(cat_tools.CAT_PROFILE_NAMES, ("memoq", "trados"))
        trados = cat_tools.get_profile("trados")
        self.assertEqual(trados["display_name"], "Trados Studio")
        self.assertIn("4.8", trados["runtime_expectation"])
        self.assertTrue(
            all(b.startswith("https://bugzilla.winehq.org/show_bug.cgi?id=")
                for b in trados["winehq_bugs"])
        )
        memoq = cat_tools.get_profile("memoq")
        self.assertIn("bug.cgi?id=56929", memoq["winehq_bugs"][0])

    def test_unknown_or_non_string_profile_returns_none(self):
        self.assertIsNone(cat_tools.get_profile("word"))
        self.assertIsNone(cat_tools.get_profile(None))
        self.assertIsNone(cat_tools.get_profile(42))

    def test_profile_lookup_is_case_insensitive(self):
        self.assertEqual(
            cat_tools.get_profile("TRADOS")["display_name"],
            "Trados Studio",
        )


class SignatureHintsTests(unittest.TestCase):

    def test_all_documented_signature_families_detected(self):
        hints = cat_tools.signature_hints(SAMPLE_LOG)
        detected = {h["hint"] for h in hints}
        self.assertEqual(
            detected,
            {"dotnet_runtime_missing", "assembly_load_failure",
             "vc_runtime_missing", "legacy_dll_missing"},
        )

    def test_counts_are_aggregated_and_confidence_is_medium(self):
        hints = {h["hint"]: h for h in cat_tools.signature_hints(SAMPLE_LOG)}
        self.assertGreaterEqual(hints["dotnet_runtime_missing"]["matches"], 2)
        for hint in hints.values():
            self.assertEqual(hint["confidence"], "medium")

    def test_every_hint_carries_a_suggestion_only_winetricks_verb(self):
        for hint in cat_tools.signature_hints(SAMPLE_LOG):
            suggestion = hint["winetricks_suggestion"]
            self.assertTrue(suggestion.startswith("winetricks "))
            # Policy guard: no execution sugar ever leaks into suggestions.
            self.assertNotIn("|", suggestion)
            self.assertNotIn("&", suggestion)
            self.assertNotIn(";", suggestion)
            self.assertNotIn("$(", suggestion)

    def test_clean_text_yields_no_hints(self):
        self.assertEqual(cat_tools.signature_hints("all good, no errors"), [])

    def test_non_string_input_rejected(self):
        with self.assertRaises(ValueError):
            cat_tools.signature_hints(None)
        with self.assertRaises(ValueError):
            cat_tools.signature_hints(123)

    def test_no_raw_log_text_leaks_into_output(self):
        hints = cat_tools.signature_hints(SAMPLE_LOG)
        blob = json.dumps(hints)
        for raw_fragment in ("mscoree", "TradosStudio.exe", "LoaderLoop",
                             "msvcp140", "import_dll"):
            self.assertNotIn(raw_fragment, blob)


class RefineFindingsTests(unittest.TestCase):

    def test_known_codes_refine_to_cat_hints(self):
        refined = {f["code"]: f for f in
                   cat_tools.refine_findings(GENERIC_FINDINGS, "trados")}
        self.assertEqual(refined["0x80131700"]["cat_hint"],
                         "dotnet_runtime_missing")
        self.assertEqual(refined["0xC0000135"]["cat_hint"],
                         "assembly_load_failure")
        self.assertEqual(refined["0x8007007E"]["cat_hint"],
                         "assembly_load_failure")
        for item in refined.values():
            if item["cat_hint"]:
                self.assertTrue(item["cat_winetricks_suggestion"].startswith("winetricks "))

    def test_input_dicts_are_never_mutated(self):
        original = [dict(item) for item in GENERIC_FINDINGS]
        cat_tools.refine_findings(GENERIC_FINDINGS, "memoq")
        self.assertEqual(original, GENERIC_FINDINGS)

    def test_unknown_profile_returns_unchanged_copies(self):
        findings = extract_error_codes("0xC0000005")
        refined = cat_tools.refine_findings(findings, "not-a-profile")
        self.assertEqual(refined, findings)
        self.assertNotIn("cat_hint", refined[0])

    def test_none_profile_is_honest_passthrough(self):
        refined = cat_tools.refine_findings(GENERIC_FINDINGS, None)
        self.assertEqual(len(refined), len(GENERIC_FINDINGS))
        self.assertNotIn("cat_hint", refined[0])

    def test_missing_module_finding_refines_under_profile(self):
        findings = extract_error_codes(
            "err:module:import_dll Library msvcr71.dll not found 0x8007007E"
        )
        missing = [f for f in findings if f["category"] == "missing_module"]
        self.assertTrue(missing)
        refined = cat_tools.refine_findings(missing, "trados")
        self.assertTrue(all(f["cat_hint"] == "assembly_load_failure"
                            for f in refined))


class DiagnoseProfileCliTests(unittest.TestCase):

    def _run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli_main(argv)
            except SystemExit as exc:  # argparse validation errors
                code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue(), err.getvalue()

    def test_diagnose_with_profile_prints_hints_and_suggestions(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "trados.log"
            log.write_text(SAMPLE_LOG, encoding="utf-8")
            code, out, _ = self._run_cli(
                ["diagnose", "--log", str(log), "--profile", "trados"]
            )
        self.assertEqual(code, 0)
        self.assertIn("CAT profile: Trados Studio", out)
        self.assertIn("dotnet_runtime_missing", out)
        self.assertIn("suggestion (NOT executed): winetricks dotnet48", out)
        self.assertIn("bugzilla.winehq.org", out)
        # Raw log text never reaches the output.
        self.assertNotIn("TradosStudio.exe", out)
        self.assertNotIn("import_dll", out)

    def test_diagnose_json_payload_carries_profile_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "memoq.log"
            log.write_text(SAMPLE_LOG, encoding="utf-8")
            code, out, _ = self._run_cli(
                ["diagnose", "--log", str(log), "--json",
                 "--profile", "memoq"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["profile"], "memoq")
        detected = {h["hint"] for h in payload["profile_hints"]}
        self.assertIn("dotnet_runtime_missing", detected)
        self.assertTrue(
            all(h["winetricks_suggestion"].startswith("winetricks ")
                for h in payload["profile_hints"])
        )

    def test_invalid_profile_is_rejected_by_argparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.txt"
            log.write_text("nothing", encoding="utf-8")
            code, _, err = self._run_cli(
                ["diagnose", "--log", str(log), "--profile", "word"]
            )
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_diagnose_without_profile_keeps_legacy_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "plain.log"
            log.write_text("0x8007007E", encoding="utf-8")
            code, out, _ = self._run_cli(["diagnose", "--log", str(log)])
        self.assertEqual(code, 0)
        self.assertNotIn("CAT profile", out)
        self.assertIn("0x8007007E", out)

    def test_unreadable_log_stays_blocked_even_with_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.log"
            code, out, _ = self._run_cli(
                ["diagnose", "--log", str(missing), "--json",
                 "--profile", "trados"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertNotIn("profile_hints", payload)
        blocked = [e for e in payload["evidence"]
                   if e["status"] == "BLOCKED"]
        self.assertTrue(blocked)


if __name__ == "__main__":
    unittest.main()
