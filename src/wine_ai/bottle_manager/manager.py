"""Safe facade over the approved Create Bottle contract and the registry.

This module provides read-only inspection and reconciliation of managed
bottle records. The public Create Bottle write path is intentionally disabled:
its same-UID mkdir/open namespace race has no safe in-process POSIX fix, and
must not be treated as an approved capability until a stronger OS boundary
is designed and independently tested.

Deliberately absent (each a separately approved future feature, never a
silent gap): initialization with ``wineboot``, ``winetricks`` recipe
execution, running executables, arbitrary commands, and deletion. No
method here spawns a process, and no method removes filesystem entries.
No public write operation is currently enabled.
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
    """Inspect managed bottle records; creation remains fail-closed and disabled."""

    def __init__(self) -> None:
        self._registry = BottleRegistry()

    @property
    def root_path(self) -> str:
        """Human-readable managed root path (reporting only; never opened by path)."""
        return str(Path.home().joinpath(*MANAGED_ROOT_PARTS))

    def create_bottle(self, name: str) -> tuple[dict[str, Any] | None, list[Evidence]]:
        """Fail closed: Create Bottle is disabled pending a safe OS design."""
        return _create_empty_bottle(name)

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
