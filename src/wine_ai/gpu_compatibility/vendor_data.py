"""Bundled, offline GPU vendor knowledge.

Loads a hand-written vendor map (``data/vendor_map.json``) that links
GPU vendors to graphics error codes commonly seen on them. The data is
offline-first: no network access, no external service, and it is
written specifically for this project.

All functions are heuristic helpers for triage; none of them prove a
root cause, and none of them execute anything.
"""

import json
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "vendor_map.json"

VALID_VENDORS = ("intel", "amd", "nvidia", "unknown")


def load_vendor_map():
    """Return the parsed vendor map (validated on load)."""
    data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("vendor map must be a JSON object")
    for vendor, entry in data.items():
        if vendor.startswith("_"):
            continue
        if vendor not in VALID_VENDORS:
            raise ValueError(f"Unknown vendor in vendor map: {vendor}")
        if not isinstance(entry, dict):
            raise ValueError("vendor entries must be objects")
        errors = entry.get("common_graphics_errors", [])
        if not isinstance(errors, list) or not all(
            isinstance(code, str) for code in errors
        ):
            raise ValueError("common_graphics_errors must be a list of strings")
    return data


def normalize_vendor(raw):
    """Map a free-form vendor string onto the allow-list.

    Substring matching absorbs common forms such as "AMD Radeon" or
    "NVIDIA Corporation". Anything unrecognised becomes "unknown".
    """
    value = (raw or "").strip().lower()
    for vendor in VALID_VENDORS:
        if vendor in value:
            return vendor
    return "unknown"


def expected_errors_for(vendor):
    """Graphics error codes commonly seen for this vendor."""
    data = load_vendor_map()
    entry = data.get(normalize_vendor(vendor), {})
    return list(entry.get("common_graphics_errors", []))


def vendor_notes(vendor):
    """Short triage notes for the vendor, or an empty string."""
    data = load_vendor_map()
    entry = data.get(normalize_vendor(vendor), {})
    notes = entry.get("notes", "")
    return notes if isinstance(notes, str) else ""


def is_plausible_graphics_error(vendor, error_code):
    """True when the code is a known common error for the vendor."""
    if not isinstance(error_code, str):
        return False
    return error_code.upper() in (
        code.upper() for code in expected_errors_for(vendor)
    )
