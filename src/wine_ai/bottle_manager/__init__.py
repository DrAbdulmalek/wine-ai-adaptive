"""Wine prefix discovery and explicitly approved bottle creation.

Discovery remains read-only. Create produces only an empty, uninitialized
managed directory; it does not run Wine or initialize a prefix.
"""
from wine_ai.bottle_manager.discovery import discover_prefixes, inspect_prefix
from wine_ai.bottle_manager.create import create_bottle

__all__ = ["discover_prefixes", "inspect_prefix", "create_bottle"]
