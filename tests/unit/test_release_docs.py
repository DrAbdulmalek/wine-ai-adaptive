"""Release-documentation contract tests (unified plan §5.1 / item 11).

These pin the honest-scope documentation of v1.0.0: the changelog must
carry an explicit scope note stating that create_bottle() is
intentionally BLOCKED in 1.0.0 — a known limitation, not a regression —
and must point at the remediation contract. STATUS.md is checked by
scripts/check_status_refs.py in CI (structural, not a unit test).
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _extract_section(text: str, heading: str) -> str:
    """Return the body of a '## <heading>' section up to the next '## '."""
    pattern = re.compile(
        r"^## \[" + re.escape(heading) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


class ChangelogScopeNoteTests(unittest.TestCase):
    def test_changelog_scope_note_present_for_1_0_0(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        section = _extract_section(text, "1.0.0")
        self.assertTrue(section, "the [1.0.0] section must exist")
        self.assertIn("### Scope note", section)
        self.assertIn("intentionally BLOCKED in 1.0.0", section)
        self.assertIn("known limitation, not a regression", section)
        self.assertIn("docs/SECURITY_REMEDIATION_STATUS.md", section)

    def test_scope_note_is_not_duplicated_in_unreleased(self):
        # The scope note documents the SHIPPED 1.0.0 boundary; it must
        # live under [1.0.0] only, so future releases do not inherit a
        # stale statement.
        text = CHANGELOG.read_text(encoding="utf-8")
        unreleased = _extract_section(text, "Unreleased")
        self.assertNotIn("intentionally BLOCKED in 1.0.0", unreleased)


if __name__ == "__main__":
    unittest.main()
