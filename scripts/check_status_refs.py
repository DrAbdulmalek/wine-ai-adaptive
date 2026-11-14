#!/usr/bin/env python3
"""STATUS.md single-source-of-truth check (unified plan §5.1, item 10).

Rules (fail-closed):
1. docs/STATUS.md must exist and carry the mandated sections:
   - the single-source declaration,
   - the PRs table,
   - the declared v1.0.0 scope section,
   - the features table,
   - the pending-decisions list.
2. No other documentation file may host a feature-status table row
   (| diagnose | / | recommend | / | create bottle | / | Trados/memoQ |)
   without referencing STATUS.md in the same file. Status lives in ONE
   place; everything else links to it.

Exit 0 = consistent; exit 1 = violations listed on stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = REPO_ROOT / "docs" / "STATUS.md"

REQUIRED_MARKERS = (
    "single source of truth",
    "## PRs",
    "النطاق المعلن لـ 1.0.0",
    "## الميزات",
    "## قرارات معلقة",
)

# A feature-status table row: the FIRST cell is exactly the feature name.
FEATURE_ROW = re.compile(
    r"^\s*\|\s*(diagnose|recommend|create bottle|trados\s*/\s*memoq)\s*\|",
    re.IGNORECASE,
)

# Files that may discuss state without hosting the feature table.
EXCLUDED = {"STATUS.md", "CHANGELOG.md", "compatibility-matrix.json"}


def check() -> list[str]:
    problems: list[str] = []

    if not STATUS_PATH.exists():
        return ["docs/STATUS.md is missing — it is the single source of truth"]

    status_text = STATUS_PATH.read_text(encoding="utf-8")
    for marker in REQUIRED_MARKERS:
        if marker not in status_text:
            problems.append(f"docs/STATUS.md missing required section: {marker!r}")

    docs_dir = REPO_ROOT / "docs"
    for md in sorted(docs_dir.rglob("*.md")):
        if md.name in EXCLUDED:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"unreadable doc {md.relative_to(REPO_ROOT)}: {exc}")
            continue
        references_status = "STATUS.md" in text
        for line_number, line in enumerate(text.splitlines(), start=1):
            if FEATURE_ROW.match(line) and not references_status:
                problems.append(
                    f"{md.relative_to(REPO_ROOT)}:{line_number} hosts a "
                    "feature-status row but does not reference STATUS.md "
                    "(single source of truth rule)"
                )
                break  # one complaint per file is enough
    return problems


def main(argv: list[str]) -> int:
    problems = check()
    if problems:
        print("STATUS.md single-source check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("STATUS.md single-source check OK: "
          "required sections present; no unlinked status tables in docs/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
