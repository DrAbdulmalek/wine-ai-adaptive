#!/usr/bin/env python3
"""Check the CAT compatibility matrix (unified plan §5.2, item 12).

Rules (fail-closed):
- The matrix file must exist and parse, with the documented structure.
- A stage that is ``null`` is UNPROVEN: fine.
- A filled stage must carry ``result`` (PASS|FAIL|BLOCKED), an ISO-8601
  ``date``, and a ``log`` path that EXISTS relative to the repository
  root — a PASS without a present log is exactly the false claim this
  check exists to prevent.
- The top-level ``status`` may only leave "UNPROVEN" when EVERY stage of
  EVERY application is documented (with an existing log).

Exit 0 = consistent; exit 1 = violation (message lists every problem).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = REPO_ROOT / "docs" / "cat" / "compatibility-matrix.json"

ALLOWED_RESULTS = {"PASS", "FAIL", "BLOCKED"}
REQUIRED_STAGE_KEYS = {"result", "log", "date"}
REQUIRED_TOP_KEYS = {"schema_version", "status", "applications", "notes"}
REQUIRED_APP_KEYS = {"versions", "wine_versions", "prefix_arch", "stages"}


def _check_stage(app: str, stage: str, entry, problems: list[str]) -> bool:
    """Return True if the stage is documented (non-null)."""
    if entry is None:
        return False
    if not isinstance(entry, dict):
        problems.append(
            f"{app}.{stage}: a filled stage must be an object with "
            f"keys {sorted(REQUIRED_STAGE_KEYS)}, got {type(entry).__name__}"
        )
        return True
    missing = REQUIRED_STAGE_KEYS - set(entry)
    if missing:
        problems.append(f"{app}.{stage}: missing keys {sorted(missing)}")
        return True
    result = entry["result"]
    if result not in ALLOWED_RESULTS:
        problems.append(
            f"{app}.{stage}: result must be one of {sorted(ALLOWED_RESULTS)}, "
            f"got {result!r}"
        )
    log_path = entry["log"]
    if not isinstance(log_path, str) or not log_path:
        problems.append(f"{app}.{stage}: log must be a non-empty path string")
    else:
        resolved = (REPO_ROOT / log_path).resolve()
        if not resolved.exists():
            problems.append(
                f"{app}.{stage}: log file does not exist: {log_path}"
            )
    date = entry["date"]
    try:
        datetime.fromisoformat(str(date).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        problems.append(f"{app}.{stage}: date must be ISO-8601, got {date!r}")
    return True


def check_matrix(path: Path) -> list[str]:
    """Validate the matrix file; return a list of problems (empty = OK)."""
    problems: list[str] = []
    if not path.exists():
        return [f"matrix file not found: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"matrix file unreadable/invalid JSON: {exc}"]

    problems.extend(
        f"missing top-level key: {key}"
        for key in REQUIRED_TOP_KEYS - set(data)
    )
    if problems:
        return problems

    applications = data["applications"]
    if not isinstance(applications, dict) or not applications:
        return ["applications must be a non-empty object"]

    all_documented = True
    documented_count = 0
    total_stages = 0
    for app, spec in applications.items():
        missing_app_keys = REQUIRED_APP_KEYS - set(spec)
        if missing_app_keys:
            problems.append(f"{app}: missing keys {sorted(missing_app_keys)}")
            continue
        stages = spec["stages"]
        if not isinstance(stages, dict) or not stages:
            problems.append(f"{app}: stages must be a non-empty object")
            continue
        for stage, entry in stages.items():
            total_stages += 1
            if _check_stage(app, stage, entry, problems):
                documented_count += 1
            else:
                all_documented = False

    status = data["status"]
    if status != "UNPROVEN":
        if not all_documented:
            problems.append(
                f"status={status!r} is forbidden: top-level status may leave "
                "'UNPROVEN' only after EVERY stage of EVERY application is "
                f"documented (documented {documented_count}/{total_stages})"
            )
        elif documented_count == 0:
            problems.append(
                f"status={status!r} is forbidden: no stages are documented"
            )
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_MATRIX
    problems = check_matrix(path)
    if problems:
        print(f"CAT matrix check FAILED ({path}):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"CAT matrix check OK ({path}): all stages null or documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
