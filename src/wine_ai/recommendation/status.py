"""Recommendation status contract (plan §2.3) — shared by the stub entry.

The engine does not exist yet (deferred until a real trust anchor is
decided, unified plan §3). This module pins the ONLY status the stub
may ever truthfully report: NO_PROVEN_EVIDENCE, stated explicitly.
"""

from __future__ import annotations

import json

__all__ = ["STATUS", "WARNINGS", "DOC_URL", "build_status_payload", "main"]

#: The only status this stub can ever truthfully report.
STATUS = "NO_PROVEN_EVIDENCE"

WARNINGS: tuple[str, ...] = (
    "PROVEN writes are blocked at the storage layer pending a real verifier. "
    "This is expected behavior, not a fault. "
    "See docs/SECURITY_REMEDIATION_STATUS.md",
)

DOC_URL = "docs/SECURITY_REMEDIATION_STATUS.md"


def build_status_payload() -> dict:
    """Return the contract payload (plan §2.3 shape, verbatim fields)."""
    return {
        "status": STATUS,
        "warnings": list(WARNINGS),
    }


def main() -> int:
    """Print the status payload as JSON; read-only, exits 0."""
    print(json.dumps(build_status_payload(), ensure_ascii=False, indent=2))
    return 0
