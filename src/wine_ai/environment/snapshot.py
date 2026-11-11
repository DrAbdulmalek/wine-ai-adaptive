"""Read-only environment snapshots for Wine diagnostics.

Inspired by the prefix/launcher context collection of tools like
winetop, reimplemented from scratch with the standard library only.

The collector is strictly read-only: it never modifies anything, never
reads personal files, and never stores full prefix paths. The prefix is
reduced to an abstract display name (its base name), which is the only
value safe enough to persist as ``prefix_id``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

WINE_VERSION_RE = re.compile(r"wine[- ](\d+\.\d+(?:\.\d+)?)")

# Environment markers that identify how the Wine session was started.
LAUNCHER_MARKERS = {
    "lutris": ("LUTRIS_GAME_UUID", "LUTRIS_"),
    "bottles": ("BOTTLES_",),
    "heroic": ("HEROIC_",),
    "steam": ("SteamAppId", "STEAM_COMPAT_DATA_PATH"),
}

LAUNCHERS = ("lutris", "bottles", "heroic", "steam", "unknown")

OS_RELEASE_PATH = Path("/etc/os-release")

DISTRO_RE = re.compile(r'^ID="?([a-z0-9._-]+)"?', re.MULTILINE)


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """Structured, path-free environment context."""

    wine_version: str          # e.g. "11.19" or "unknown"
    prefix_name: str           # abstract base name, never a full path
    launcher: str              # lutris | bottles | heroic | steam | unknown
    distro: str                # os-release ID only, e.g. "manjaro"

    def __post_init__(self):
        for field_name in ("wine_version", "prefix_name", "distro"):
            value = getattr(self, field_name)
            if not value or "/" in value or "\\" in value:
                raise ValueError(f"Unsafe {field_name} in snapshot")
        if self.launcher not in LAUNCHERS:
            raise ValueError("Unsafe launcher in snapshot")

    def to_record_fields(self) -> dict:
        """Fields acceptable by KnowledgeStore records."""
        return {
            "wine_version": self.wine_version,
            "distro": self.distro,
        }

    def to_prefix_id(self) -> Optional[str]:
        """Abstract prefix identifier, or None when unknown."""
        if self.prefix_name == "unknown":
            return None
        return self.prefix_name

    def to_dict(self) -> dict:
        return {
            "wine_version": self.wine_version,
            "prefix_name": self.prefix_name,
            "launcher": self.launcher,
            "distro": self.distro,
        }


def detect_launcher(env: Mapping[str, str]) -> str:
    """Detect the launcher from environment variable names only.

    Values are deliberately not inspected: names alone identify the
    launcher and avoid copying any user data. Markers ending with an
    underscore match any variable name *starting* with them (e.g.
    ``BOTTLES_`` covers ``BOTTLES_ENV``).
    """
    if not isinstance(env, Mapping):
        raise ValueError("env must be a mapping")
    keys = tuple(env.keys())
    for name, markers in LAUNCHER_MARKERS.items():
        for marker in markers:
            if marker.endswith("_"):
                if any(key.startswith(marker) for key in keys):
                    return name
            elif marker in keys:
                return name
    return "unknown"


def parse_wine_version(text: str) -> Optional[str]:
    """Extract a bare Wine version like "11.19" from tool output."""
    if not isinstance(text, str):
        return None
    match = WINE_VERSION_RE.search(text)
    return match.group(1) if match else None


def detect_wine_version(env: Mapping[str, str]) -> Optional[str]:
    """Read-only Wine version detection.

    Checks WINE_VERSION-style environment hints first, then falls back
    to running ``wine --version`` with a short timeout. Never raises:
    an unavailable Wine simply yields ``None``.
    """
    hint = env.get("WINE_VERSION", "") if isinstance(env, Mapping) else ""
    version = parse_wine_version(hint)
    if version:
        return version

    executable = shutil.which("wine")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_wine_version(result.stdout)


def distro_name(path: os.PathLike = OS_RELEASE_PATH) -> str:
    """Return the os-release ID (e.g. "manjaro") or "unknown".

    Only the short ID is returned: no fullPRETTY_NAME, no URLs, no
    other fields from the file.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "unknown"
    match = DISTRO_RE.search(text)
    return match.group(1) if match else "unknown"


def collect(
    env: Optional[Mapping[str, str]] = None,
    prefix_path: Optional[str] = None,
) -> EnvironmentSnapshot:
    """Collect a structured snapshot of the current environment.

    ``prefix_path`` is reduced to its base name before anything is
    stored; the full path is never kept in memory beyond this call.
    """
    env = env if env is not None else os.environ

    wine_version = detect_wine_version(env) or "unknown"
    launcher = detect_launcher(env)
    distro = distro_name()

    if prefix_path:
        prefix_name = Path(prefix_path).name or "unknown"
    else:
        prefix_name = "unknown"

    return EnvironmentSnapshot(
        wine_version=wine_version,
        prefix_name=prefix_name,
        launcher=launcher,
        distro=distro,
    )
