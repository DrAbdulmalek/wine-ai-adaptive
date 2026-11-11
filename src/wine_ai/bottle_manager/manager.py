"""Safe facade over the approved Create Bottle contract and the registry.

This module is the Phase-B rebuild of the 2026-11-11
``feature/bottle-manager`` draft. Per the owner's independent review
disposition (2026-11-11), the rebuild keeps exactly three capabilities:

- ``create_bottle`` — the audited, descriptor-relative Create-Empty-Bottle
  operation (:mod:`wine_ai.bottle_manager.create`), followed by an
  identity-pinned registration in the managed registry
  (:mod:`wine_ai.bottle_manager.registry`);
- read-only inspection of registered bottles;
- reconciliation (``verify_bottle`` / ``orphans``) between the registry
  and the managed root.

Deliberately absent (each a separately approved future feature, never a
silent gap): initialization with ``wineboot``, ``winetricks`` recipe
execution, running executables, arbitrary commands, and deletion. No
method here spawns a process, and no method removes filesystem entries.
If registration fails after the directory was created, the operation
returns a ``BLOCKED`` evidence and reports the directory as an orphan —
state is never silently inconsistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wine_ai.bottle_manager.create import (
    MANAGED_ROOT_PARTS,
    create_bottle as _create_empty_bottle,
)
from wine_ai.bottle_manager.registry import (
    BottleRegistry,
    BottleRegistryError,
)
from wine_ai.evidence import Evidence, EvidenceStatus

__all__ = [
    "BottleManager",
    "MANAGED_ROOT_PARTS",
]

class BottleManager:
    """Manage empty, uninitialized bottle directories under the managed root."""

    def __init__(self) -> None:
        self._registry = BottleRegistry()

    # -- helpers -------------------------------------------------------------

    @property
    def root_path(self) -> str:
        """Human-readable managed root path (reporting only; never opened by path)."""
        return str(Path.home().joinpath(*MANAGED_ROOT_PARTS))

    @staticmethod
    def _registry_entry(record: dict[str, Any], identity: dict[str, int]) -> dict[str, Any]:
        # create.py's record does not carry a relative path; it is derived
        # from the managed root contract (last managed component + name).
        return {
            "name": record["name"],
            "relative_path": f"{MANAGED_ROOT_PARTS[-1]}/{record['name']}",
            "state": record["state"],
            "created_at": record["created_at"],
            "operation_id": record["operation_id"],
            "identity": dict(identity),
        }

    def _capture_identity(self, name: str) -> dict[str, int]:
        """Pin the created directory's identity through the registry's verified root."""
        return self._registry.pin_identity(name)

    # -- write capability (create only) -----------------------------------------

    def create_bottle(self, name: str) -> tuple[dict[str, Any] | None, list[Evidence]]:
        """Create one empty 0700 directory and register its pinned identity.

        No Wine, no winetricks, no subprocess, no deletion. The returned
        record carries ``state == EMPTY_UNINITIALIZED`` and the pinned
        ``(dev, ino)`` identity used by :meth:`verify_bottle`.
        """
        record, evidences = _create_empty_bottle(name)
        if record is None:
            return None, evidences
        try:
            identity = self._capture_identity(record["name"])
        except (OSError, BottleRegistryError) as exc:
            evidences = evidences + [
                Evidence(
                    "bottle_register",
                    EvidenceStatus.BLOCKED,
                    f"Created directory could not be pinned to the registry; "
                    f"it is left unregistered and must be reconciled manually ({exc})",
                    "bottle_manager.manager",
                    {"operation_id": record.get("operation_id"), "name": record.get("name")},
                )
            ]
            return None, evidences
        entry = self._registry_entry(record, identity)
        try:
            self._registry.register(entry)
        except BottleRegistryError as exc:
            evidences = evidences + [
                Evidence(
                    "bottle_register",
                    EvidenceStatus.BLOCKED,
                    f"Registry registration failed; the created directory is left "
                    f"unregistered and must be reconciled manually ({exc})",
                    "bottle_manager.manager",
                    {"operation_id": record.get("operation_id"), "name": record.get("name")},
                )
            ]
            return None, evidences
        return entry, evidences

    # -- read-only capabilities ---------------------------------------------------

    def list_bottles(self) -> dict[str, dict[str, Any]]:
        """Return a copy of all registered bottle records (empty when uninitialized)."""
        return self._registry.read()

    def get_bottle(self, name: str) -> dict[str, Any]:
        """Return one registered bottle record."""
        return self._registry.get(name)

    def verify_bottle(self, name: str) -> dict[str, Any]:
        """Revalidate a registered bottle's on-disk identity (never repairs)."""
        return self._registry.verify(name)

    def orphans(self) -> dict[str, Any]:
        """List disagreements between the registry and the managed root."""
        return self._registry.orphans()
