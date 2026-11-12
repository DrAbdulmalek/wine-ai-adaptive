"""Documentation claim invariants (reviewer-corrected narrow invariants).

Replaces the vague "no document may repeat status" rule with five
testable invariants over docs/ and the root Markdown files. Each test
bans only AFFIRMATIVE claim phrasings; negated statements ("Do not
claim...", "لا يُغلق", "Avoid saying...") are legal and are excluded by
explicit negation guards, so the honest-limit documentation itself never
trips the checker.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Root-level Markdown files plus everything under docs/.
def _doc_files() -> list[Path]:
    files = [p for p in REPO_ROOT.glob("*.md") if p.is_file()]
    files.extend(sorted((REPO_ROOT / "docs").rglob("*.md")))
    return sorted(set(files))


def _violations(patterns: list[re.Pattern[str]], guards: tuple[str, ...]) -> list[str]:
    problems: list[str] = []
    for path in _doc_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        rel = path.relative_to(REPO_ROOT)
        for number, line in enumerate(lines, start=1):
            if any(guard.lower() in line.lower() for guard in guards):
                continue
            for pattern in patterns:
                if pattern.search(line):
                    problems.append(
                        f"{rel}:{number}: banned claim {pattern.pattern!r} "
                        f"in line: {line.strip()[:120]}"
                    )
                    break
    return problems


NEGATION_GUARDS = (
    "do not", "don't", "never", "not ", "no ", "cannot", "can't",
    "unresolved", "remains open", "blocked", "disabled", "untested",
    "غير ", "لا ", "لن ", "ألا ", "ليس ", "مفتوح", "معطّل", "محجوب",
)


class DocClaimInvariantTests(unittest.TestCase):
    """Reviewer invariants 1-5 as fail-closed document scans."""

    def _assert_no_claims(self, patterns: list[str], guards: tuple[str, ...] = NEGATION_GUARDS):
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        problems = _violations(compiled, guards)
        self.assertEqual(
            problems, [],
            "documents must not contain the banned affirmative claims:\n"
            + "\n".join(problems),
        )

    def test_docs_do_not_claim_create_enabled(self):
        # Invariant 1: create_bottle stays BLOCKED in every document.
        self._assert_no_claims([
            r"create_bottle\(\)?\s{0,10}(is|now)\s+(enabled|available|unblocked|active|allowed)",
            r"create\s+bottle\s+(is|now)\s+(enabled|available|unblocked|active)",
            r"\benabling\s+create_bottle\b",
            r"create_bottle\s+مفعّل",
        ])

    def test_docs_do_not_claim_same_uid_closed(self):
        # Invariant 2: the same-UID registry race stays OPEN in every
        # document.
        self._assert_no_claims([
            r"same.?uid[^\n]{0,40}\b(is|are|was|has\s+been)\s+(now\s+)?(closed|fixed|resolved|mitigated)\b",
            r"closes\s+the\s+same.?uid",
            r"same.?uid\s+race\s+closed",
            r"same.?uid\s+مغلق",
        ])

    def test_docs_do_not_claim_cas_safe(self):
        # Invariant 3: no document may describe the publication protocol
        # as CAS.
        self._assert_no_claims([
            r"\b(provides?|offers?|gives?)\s+(a\s+)?CAS\b",
            r"\bCAS[-\s]safe\b",
            r"\batomic\s+CAS\b",
            r"\bis\s+an?\s+atomic\s+compare-and-swap\b",
            r"\bprovides?\s+compare-and-swap\b",
            r"\bCAS\s+على\s+النشر\b",
        ])

    def test_docs_do_not_claim_cat_proven_without_matrix(self):
        # Invariant 4: Trados/memoQ must never be claimed PROVEN /
        # supported / compatible affirmatively. The compatibility matrix
        # (docs/cat/compatibility-matrix.json) is the only evidence
        # authority; it is all-UNPROVEN, so no affirmative claim may
        # exist anywhere.
        self._assert_no_claims([
            r"(Trados|memoQ)[^\n]{0,60}\b(is|are)\s+(PROVEN|supported|compatible|validated|verified)\b",
            r"\b(supports|runs)\s+(Trados|memoQ)\b",
            r"(Trados|memoQ)[^\n]{0,30}(مدعوم|متوافق)\s+(بشكل\s+)?كامل",
        ])

    def test_docs_do_not_claim_manjaro_validated_without_evidence(self):
        # Invariant 5: Manjaro validation claims require an uploaded
        # sanitized evidence report. Without one, no document may claim
        # validation.
        evidence = list((REPO_ROOT / "evidence").glob("manjaro-*.txt")) if (REPO_ROOT / "evidence").exists() else []
        evidence.append(REPO_ROOT / "manjaro-smoke-report.txt")
        has_evidence = any(p.exists() for p in evidence)
        if has_evidence:
            self.skipTest(
                "Manjaro evidence report present; validation claims may exist"
            )
        self._assert_no_claims([
            r"Manjaro[^\n]{0,50}\b(is|has\s+been|was)\s+(validated|verified|tested\s+ok|compatible)\b",
            r"\b(validated|verified)\s+on\s+Manjaro\b",
            r"Manjaro[^\n]{0,40}(متحقق|مُختبر بنجاح)",
        ])


if __name__ == "__main__":
    unittest.main()
