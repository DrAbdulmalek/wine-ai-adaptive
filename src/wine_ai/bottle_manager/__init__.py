"""Read-only Wine prefix management (CrossOver clean-room spec, P0 tier).

Only discovery/inspection live here for now. Write operations
(create/clone/backup/restore/delete) are deliberately absent until a
separate approval layer, threat-model entries and tests exist — see
``docs/CROSSOVER_CLEAN_ROOM_SPEC.md`` section 6.4.
"""

from wine_ai.bottle_manager.discovery import discover_prefixes, inspect_prefix

__all__ = ["discover_prefixes", "inspect_prefix"]
