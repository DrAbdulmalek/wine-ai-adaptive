"""Wine prefix management (CrossOver clean-room spec, P0 tier).

Discovery/inspection, the approved Create-Empty-Bottle operation and the
managed registry live here. Write operations beyond empty-directory
creation (clone/backup/restore/delete/initialize/run) are deliberately
absent until a separate approval layer, threat-model entries and tests
exist — see ``docs/CROSSOVER_CLEAN_ROOM_SPEC.md`` section 6.4 and the
2026-11-11 disposition of the rejected ``feature/bottle-manager`` draft
(``docs/reviews/EXTERNAL_REVIEWS.md`` section 11).
"""

from wine_ai.bottle_manager.create import (
    MANAGED_ROOT_PARTS,
    BottleCreateError,
    create_bottle,
)
from wine_ai.bottle_manager.discovery import discover_prefixes, inspect_prefix
from wine_ai.bottle_manager.manager import BottleManager
from wine_ai.bottle_manager.registry import (
    BottleRegistry,
    BottleRegistryError,
    REGISTRY_NAME,
    STATE_EMPTY_UNINITIALIZED,
)

__all__ = [
    "MANAGED_ROOT_PARTS",
    "BottleCreateError",
    "create_bottle",
    "discover_prefixes",
    "inspect_prefix",
    "BottleManager",
    "BottleRegistry",
    "BottleRegistryError",
    "REGISTRY_NAME",
    "STATE_EMPTY_UNINITIALIZED",
]
