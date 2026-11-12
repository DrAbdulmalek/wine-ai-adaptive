"""Read-only environment snapshots for Wine diagnostics.

Inspired by the prefix/launcher context collection of tools like
winetop, reimplemented from scratch with the standard library only.

Privacy contract
----------------
The collector never modifies anything, never reads personal files, and
never stores full prefix paths. The prefix is reduced to an abstract
display name (its base name), the only value safe enough to persist as
``prefix_id``. The absolute path can additionally be reduced to
``prefix_hash``: a truncated *HMAC-SHA256* pseudonym keyed by a local
machine identifier. This enables correlation across sessions on the
same machine. The hash alone does not reveal the path, but resistance to
offline guessing depends on not disclosing the machine identifier with
it. Stated limits: it is NOT anonymity; same-
machine linking is by design; any local process able to read the
machine identifier can recompute it; independently generated machine
identifiers normally produce different pseudonyms, but cloned images may
share an identifier and truncated hashes have a non-zero collision
probability; and the value is excluded from the export allow-list. When no machine
identifier is readable, no pseudonym is produced at all (fail-closed).

Runtime contract
----------------
"Read-only" describes effects on the Wine prefix and the diagnostic
subject: nothing is modified. It does NOT mean "no external programs":
``detect_wine_version`` (and therefore ``collect``) may request the
allow-listed ``wine --version`` query when no ``WINE_VERSION``-style
environment hint is present. The central command gate validates the
resolved executable and exact argument. Pass ``allow_exec=False`` to forbid
subprocess execution entirely (environment hints only).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from wine_ai.command import run_read_only

WINE_VERSION_RE = re.compile(r"wine[- ](\d+\.\d+(?:\.\d+)?)")

# Environment markers that identify how the Wine session was started.
LAUNCHER_MARKERS = {
    "lutris": ("LUTRIS_GAME_UUID", "LUTRIS_"),
    "bottles": ("BOTTLES_",),
    "heroic": ("HEROIC_",),
    "steam": ("SteamAppId", "STEAM_COMPAT_DATA_PATH"),
}

LAUNCHERS = ("lutris", "bottles", "heroic", "steam", "wine", "unknown")

# A bare Wine session (WINEPREFIX set, no launcher marker) is reported
# as the explicit "wine" launcher value instead of collapsing into the
# not-detected "unknown" value.
BARE_WINE_LAUNCHER = "wine"

# Length of the truncated HMAC-SHA256 prefix pseudonym (hex
# characters, 128 bits). The store's CHECK (16..64 lowercase hex)
# accepts this length unchanged.
PREFIX_HASH_CHARS = 32

# Domain-separation label for deriving the local pseudonym key, so the
# key can never collide with any other use of the machine identifier.
_PREFIX_HASH_KEY_LABEL = b"wine-ai-adaptive/prefix-hash/v1"

# Local machine identifiers used to derive the pseudonym key. They are
# read only, never stored and never leave the machine. If none is
# readable, no pseudonym is produced at all (fail-closed).
_MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")

OS_RELEASE_PATH = Path("/etc/os-release")

DISTRO_RE = re.compile(r'^ID="?([a-z0-9._-]+)"?', re.MULTILINE)


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """Structured, path-free environment context."""

    wine_version: str          # e.g. "11.19" or "unknown"
    prefix_name: str           # abstract base name, never a full path
    launcher: str              # lutris | bottles | heroic | steam | wine | unknown
    distro: str                # os-release ID only, e.g. "manjaro"
    prefix_hash: Optional[str] = None  # machine-keyed HMAC of abs path

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

    def to_launcher(self) -> Optional[str]:
        """Allow-listed launcher name for store records, or None."""
        if self.launcher == "unknown":
            return None
        return self.launcher

    def to_prefix_hash(self) -> Optional[str]:
        """Prefix path pseudonym, or None when unknown."""
        return self.prefix_hash

    def to_dict(self) -> dict:
        return {
            "wine_version": self.wine_version,
            "prefix_name": self.prefix_name,
            "launcher": self.launcher,
            "distro": self.distro,
            "prefix_hash": self.prefix_hash,
        }


def detect_launcher(
    env: Mapping[str, str], *, prefix_declared: bool = False,
) -> str:
    """Detect the launcher from environment variable names only.

    Values are deliberately not inspected: names alone identify the
    launcher and avoid copying any user data. Markers ending with an
    underscore match any variable name *starting* with them (e.g.
    ``BOTTLES_`` covers ``BOTTLES_ENV``).

    ``prefix_declared`` reports that a Wine prefix is in use (e.g.
    ``WINEPREFIX`` is set): a session without any launcher marker is
    then the explicit bare-Wine value ``"wine"`` instead of the
    not-detected value ``"unknown"``.
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
    if prefix_declared:
        return BARE_WINE_LAUNCHER
    return "unknown"


def _prefix_hash_key() -> Optional[bytes]:
    """Derive the local pseudonym key from the machine identifier.

    The identifier itself never leaves the machine and is never stored;
    only a domain-separated SHA-256 mixture of it serves as the HMAC
    key. Returns ``None`` when no identifier is readable: fail-closed,
    no pseudonym at all.
    """
    for path in _MACHINE_ID_PATHS:
        try:
            raw = Path(path).read_bytes().strip()
        except OSError:
            continue
        if raw:
            return hashlib.sha256(
                _PREFIX_HASH_KEY_LABEL + b"\x00" + raw
            ).digest()
    return None


def prefix_hash_of(
    prefix_path: str, key: Optional[bytes] = None,
) -> Optional[str]:
    """Machine-keyed HMAC pseudonym of a prefix path, or None.

    The absolute path is hashed and never stored or returned: 32
    lowercase hex characters (128 bits of HMAC-SHA256) keyed by the
    local machine identifier allow correlation across sessions on
    machines with the same machine identifier. Independently generated
    machine identifiers normally produce different pseudonyms, but cloned
    images may share machine-id values and 128-bit truncation has a
    non-zero collision probability. The machine-id is readable by local
    processes, so it is not a secret against a local attacker; external
    path-guessing resistance depends on not exporting the machine-id with
    the hash. This is pseudonymization, not globally unique device identity.

    ``key`` overrides the derived local key (tests use this). Without
    any usable key the pseudonym is not produced at all (fail-closed).
    The input path is never kept in memory beyond this call.
    """
    if not isinstance(prefix_path, str) or not prefix_path.strip():
        return None
    if key is None:
        key = _prefix_hash_key()
    if not isinstance(key, (bytes, bytearray)) or not key:
        return None
    try:
        absolute = os.path.abspath(prefix_path)
    except (OSError, ValueError):
        return None
    digest = hmac.new(
        bytes(key), absolute.encode("utf-8", "replace"), hashlib.sha256
    ).hexdigest()
    return digest[:PREFIX_HASH_CHARS]


def parse_wine_version(text: str) -> Optional[str]:
    """Extract a bare Wine version like "11.19" from tool output."""
    if not isinstance(text, str):
        return None
    match = WINE_VERSION_RE.search(text)
    return match.group(1) if match else None


def detect_wine_version(
    env: Mapping[str, str], *, allow_exec: bool = True,
) -> Optional[str]:
    """Read-only Wine version detection.

    Checks WINE_VERSION-style environment hints first. When no hint is
    present and ``allow_exec`` is True (the default), it runs ``wine
    --version`` once with a short timeout — one short-lived external
    program, nothing modified. With ``allow_exec=False`` no subprocess
    is ever spawned: environment hints only, otherwise ``None``.
    Never raises: an unavailable Wine simply yields ``None``.
    """
    hint = env.get("WINE_VERSION", "") if isinstance(env, Mapping) else ""
    version = parse_wine_version(hint)
    if version:
        return version

    if not allow_exec:
        return None

    result = run_read_only(["wine", "--version"], timeout=5)
    if result.returncode != 0:
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
    *,
    allow_exec: bool = True,
) -> EnvironmentSnapshot:
    """Collect a structured snapshot of the current environment.

    ``prefix_path`` is reduced to its base name before anything is
    stored; the full path is never kept in memory beyond the hash call.
    ``prefix_hash`` is the machine-keyed HMAC pseudonym produced by
    ``prefix_hash_of`` (local cross-session correlation; never
    exported). ``allow_exec`` controls whether ``wine --version`` may
    be executed when no environment hint is present (see
    ``detect_wine_version``): True (default) allows the one external
    call, False forbids any subprocess.
    """
    env = env if env is not None else os.environ

    wine_version = detect_wine_version(env, allow_exec=allow_exec) or "unknown"
    prefix_declared = bool(
        isinstance(env, Mapping) and env.get("WINEPREFIX", "").strip()
    ) or bool(prefix_path)
    launcher = detect_launcher(env, prefix_declared=prefix_declared)
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
        prefix_hash=prefix_hash_of(prefix_path) if prefix_path else None,
    )
