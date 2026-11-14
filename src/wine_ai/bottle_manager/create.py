"""Fail-closed Create Bottle API.

Bottle creation is disabled until a same-UID namespace race can be eliminated
by a stronger OS boundary. POSIX mkdirat() does not return a descriptor for
the newly created inode, so mkdirat()+openat() cannot prove that the opened
object is the object created if another process with the same UID can rename
directory entries between the calls. No filesystem writes are performed.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from wine_ai.evidence import Evidence, EvidenceStatus

__all__ = [
    "create_bottle",
    "MANAGED_ROOT_PARTS",
    "NAME_PATTERN",
    "BLOCKED_REASON",
    "BLOCKED_DOC_URL",
]

MANAGED_ROOT_PARTS = (".local", "share", "wine-ai-adaptive", "prefixes")
AUDIT_DIR_PARTS = (".local", "share", "wine-ai-adaptive", "logs")
AUDIT_FILENAME = "create-bottle.jsonl"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DIR_MODE, FILE_MODE = 0o700, 0o600

#: Explicit machine-readable reason token (unified plan §1.4, 2026-11-13):
#: the block is a product decision, not a transient fault.
BLOCKED_REASON = "CREATION_DISABLED_PENDING_SECURITY_REVIEW"
#: Where the blocking decision and its rationale are documented.
BLOCKED_DOC_URL = "docs/SECURITY_REMEDIATION_STATUS.md#النقطة-1"


class BottleCreateError(Exception):
    """A request was rejected or could not be completed safely."""


def _validate_name(name: object) -> bool:
    return isinstance(name, str) and NAME_PATTERN.fullmatch(name) is not None


def create_bottle(name: str) -> tuple[dict[str, Any] | None, list[Evidence]]:
    """Return BLOCKED until safe mkdir/open identity binding is available.

    This is an intentional feature gate, not a transient filesystem error.
    The call creates no directory, audit log, lock, Wine process, or registry
    entry, regardless of whether the supplied name is valid.
    """
    operation_id = uuid.uuid4().hex
    valid_name = name if _validate_name(name) else None
    return None, [
        Evidence(
            "bottle_create",
            EvidenceStatus.BLOCKED,
            "Create Bottle is disabled: the same-UID mkdir/open namespace race "
            "cannot currently be closed safely",
            "bottle_manager.create",
            {
                "operation_id": operation_id,
                "name": valid_name,
                "feature_state": "DISABLED_PENDING_SECURITY_DESIGN",
                "reason": BLOCKED_REASON,
                "technical_reason": (
                    "mkdirat does not return an opened descriptor for the "
                    "created inode"
                ),
                "doc_url": BLOCKED_DOC_URL,
            },
        )
    ]
