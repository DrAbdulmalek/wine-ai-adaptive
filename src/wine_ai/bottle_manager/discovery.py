"""Read-only discovery and inspection of Wine prefixes.

Implements the P0 read-only tier of ``docs/CROSSOVER_CLEAN_ROOM_SPEC.md``
(section 6.4): Discover / Inspect / Inventory / Validate — nothing else.

Hard guarantees:

- **No write operations exist in this module.** No file is created,
  modified, moved or deleted; no command is executed (no subprocess).
- Prefix candidates that are symlinks or non-directories are rejected
  instead of being followed (fail-closed).
- The registry header is read with ``O_NOFOLLOW`` and capped to a few
  bytes; anything that does not match the strict pattern stays
  ``UNPROVEN`` instead of being guessed.
- Every finding carries classified ``Evidence`` items.

The remaining check-then-use window (TOCTOU) is documented in
``docs/CROSSOVER_CLEAN_ROOM_SPEC.md`` section 14 and is not claimed to
be fully closed.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from wine_ai.evidence import Evidence, EvidenceStatus

__all__ = [
    "MAX_SCAN_SUBDIRS",
    "REG_HEADER_BYTES",
    "discover_prefixes",
    "inspect_prefix",
]

# Security/behaviour caps.
MAX_SCAN_SUBDIRS = 64
REG_HEADER_BYTES = 64

# A Wine prefix is identified by these on-disk markers (read-only facts).
MARKER_DIR = "drive_c"
MARKER_FILES = ("system.reg", "user.reg", "userdef.reg")

# Wine registry files start with an architecture header line such as
# "##arch - win64". Strict pattern; anything else stays unparsed.
_ARCH_LINE = re.compile(r"^#{1,2}\s*arch\s*-\s*win(32|64)\s*$")

_MANAGED_ROOT_PARTS = (".local", "share", "wine-ai-adaptive", "prefixes")


def _is_real_dir(path: Path) -> bool:
    """True only for a real directory (symlinks and other types fail)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode)


def _read_reg_arch(path: Path) -> tuple[str | None, str | None]:
    """Read the architecture header from ``system.reg`` — read-only.

    Returns ``(architecture, error)``. The file is opened with
    ``O_NOFOLLOW`` so a symlink swapped into place fails instead of
    being followed, and at most ``REG_HEADER_BYTES`` bytes are read.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(
        os, "O_CLOEXEC", 0
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        raw = os.read(fd, REG_HEADER_BYTES)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        os.close(fd)

    first_line = raw.decode("utf-8", errors="replace").split("\n", 1)[0]
    match = _ARCH_LINE.match(first_line.strip())
    if not match:
        return None, None  # unparsable header: stays UNPROVEN, no guess
    return f"win{match.group(1)}", None


def _inspect_candidate(
    path: Path,
    origin: str,
    protected: bool,
) -> tuple[dict | None, list[Evidence]]:
    """Validate one prefix candidate. Strictly read-only."""
    evidence: list[Evidence] = []
    source = f"bottle_manager.discovery:{origin}"

    try:
        info = os.lstat(path)
    except PermissionError as exc:
        evidence.append(Evidence(
            "prefix_candidate", EvidenceStatus.BLOCKED,
            "Candidate directory could not be examined",
            source, {"path": str(path), "error": str(exc)},
        ))
        return None, evidence
    except OSError as exc:
        evidence.append(Evidence(
            "prefix_candidate", EvidenceStatus.UNPROVEN,
            "Candidate path is not accessible",
            source, {"path": str(path), "error": str(exc)},
        ))
        return None, evidence

    if stat.S_ISLNK(info.st_mode):
        evidence.append(Evidence(
            "prefix_candidate", EvidenceStatus.BLOCKED,
            "Symlinked prefix candidate rejected (not followed)",
            source, {"path": str(path)},
        ))
        return None, evidence
    if not stat.S_ISDIR(info.st_mode):
        evidence.append(Evidence(
            "prefix_candidate", EvidenceStatus.UNPROVEN,
            "Candidate is not a directory",
            source, {"path": str(path)},
        ))
        return None, evidence

    # --- markers -------------------------------------------------------
    missing: list[str] = []
    rejected_links: list[str] = []
    for name in (MARKER_DIR, *MARKER_FILES):
        marker = path / name
        try:
            marker_info = os.lstat(marker)
        except OSError:
            missing.append(name)
            continue
        if stat.S_ISLNK(marker_info.st_mode):
            rejected_links.append(name)

    # A symlinked marker fails closed before anything else is read.
    if rejected_links:
        evidence.append(Evidence(
            "prefix_markers", EvidenceStatus.BLOCKED,
            "Symlinked registry files rejected (not followed)",
            source,
            {"path": str(path), "rejected_symlinks": rejected_links},
        ))
        return None, evidence

    if missing:
        evidence.append(Evidence(
            "prefix_markers", EvidenceStatus.UNPROVEN,
            "Path does not look like a Wine prefix (missing markers)",
            source,
            {"path": str(path), "missing": missing},
        ))
        return None, evidence

    # --- architecture ----------------------------------------------------
    architecture: str | None = None
    syswow64_present = False
    arch_conflict = False

    arch, arch_error = _read_reg_arch(path / "system.reg")
    if arch_error:
        evidence.append(Evidence(
            "prefix_architecture", EvidenceStatus.BLOCKED,
            "Registry header could not be read safely",
            source,
            {"path": str(path / "system.reg"), "error": arch_error},
        ))
    elif arch:
        architecture = arch

    syswow64 = path / MARKER_DIR / "windows" / "syswow64"
    try:
        wow_info = os.lstat(syswow64)
        if stat.S_ISLNK(wow_info.st_mode):
            evidence.append(Evidence(
                "prefix_architecture", EvidenceStatus.BLOCKED,
                "Symlinked syswow64 rejected as architecture signal",
                source, {"path": str(syswow64)},
            ))
        else:
            syswow64_present = stat.S_ISDIR(wow_info.st_mode)
    except OSError:
        syswow64_present = False

    if architecture == "win32" and syswow64_present:
        arch_conflict = True
        evidence.append(Evidence(
            "prefix_architecture", EvidenceStatus.CONTRADICTED,
            "Registry header says win32 but syswow64 exists "
            "(conflicting signals kept, not resolved by guessing)",
            source, {"path": str(path)},
        ))

    if arch_conflict:
        pass  # conflict evidence already recorded above
    elif architecture:
        evidence.append(Evidence(
            "prefix_architecture", EvidenceStatus.PROVEN,
            f"Architecture {architecture} from system.reg header",
            source, {"path": str(path)},
        ))
    else:
        evidence.append(Evidence(
            "prefix_architecture", EvidenceStatus.UNPROVEN,
            "Architecture header unparsable; left unknown",
            source, {"path": str(path)},
        ))

    return {
        "name": path.name,
        "path": str(path),
        "origin": origin,
        "protected": protected,
        "architecture": architecture,
        "arch_conflict": arch_conflict,
        "syswow64_present": syswow64_present,
        "markers": [MARKER_DIR, *MARKER_FILES],
    }, evidence


def _managed_root(home: Path) -> Path:
    result = home
    for part in _MANAGED_ROOT_PARTS:
        result = result / part
    return result


def discover_prefixes(
    home: Path | None = None,
    candidates: list[Path] | None = None,
    container_roots: list[Path] | None = None,
) -> tuple[list[dict], list[Evidence]]:
    """Discover Wine prefixes — strictly read-only.

    ``home`` overrides the base directory of the default search
    locations (used by tests). ``candidates`` adds explicit direct
    prefix paths, ``container_roots`` adds directories whose immediate
    children are scanned as prefix candidates (capped per root).

    Default locations:
    - ``$WINEPREFIX`` when set (explicit user declaration)
    - ``~/.wine`` (the default prefix — always classified ``protected``)
    - ``~/.local/share/wine-ai-adaptive/prefixes/*`` (managed, spec 6.2)
    """
    base = Path(home) if home is not None else Path.home()
    evidence: list[Evidence] = []
    results: list[dict] = []
    seen: set[str] = set()

    def emit(candidate: Path, origin: str, protected: bool) -> None:
        info, item_evidence = _inspect_candidate(
            Path(os.path.abspath(candidate)), origin, protected
        )
        evidence.extend(item_evidence)
        if info is None:
            return
        if info["path"] in seen:
            # Protection precedence (P1-D contract): re-discovering the
            # same path through any source must never lower its
            # protection status. The default prefix (``~/.wine``) is
            # therefore still flagged protected when the user happens
            # to declare it via ``$WINEPREFIX`` first.
            for existing in results:
                if (
                    existing["path"] == info["path"]
                    and info["protected"]
                    and not existing["protected"]
                ):
                    existing["protected"] = True
            return
        seen.add(info["path"])
        results.append(info)

    # 1) $WINEPREFIX: an explicit declaration, honoured when set.
    declared = os.environ.get("WINEPREFIX", "").strip()
    if declared:
        emit(Path(declared), "WINEPREFIX", protected=False)
    else:
        evidence.append(Evidence(
            "prefix_discovery", EvidenceStatus.UNPROVEN,
            "WINEPREFIX is not declared in the environment",
            "bottle_manager.discovery:WINEPREFIX",
        ))

    # 2) The default prefix: discovered, but always protected.
    emit(base / ".wine", "default_prefix", protected=True)

    # 3) Managed prefixes (spec 6.2): scan the container's children.

    def scan_container(root: Path, origin: str) -> None:
        if not _is_real_dir(root):
            evidence.append(Evidence(
                "prefix_discovery", EvidenceStatus.UNPROVEN,
                "Container root absent or not a real directory",
                f"bottle_manager.discovery:{origin}",
                {"path": str(root)},
            ))
            return
        try:
            # Every entry is passed to the fail-closed classifier —
            # symlinks and non-directories get explicit evidence instead
            # of being filtered out silently.
            children = sorted(root.iterdir())
        except OSError as exc:
            evidence.append(Evidence(
                "prefix_discovery", EvidenceStatus.BLOCKED,
                "Container root could not be listed",
                f"bottle_manager.discovery:{origin}",
                {"path": str(root), "error": str(exc)},
            ))
            return
        capped = children[:MAX_SCAN_SUBDIRS]
        if len(children) > MAX_SCAN_SUBDIRS:
            evidence.append(Evidence(
                "prefix_discovery", EvidenceStatus.PARTIALLY_PROVEN,
                "Scan capped at MAX_SCAN_SUBDIRS entries for this root",
                f"bottle_manager.discovery:{origin}",
                {
                    "path": str(root),
                    "found": len(children),
                    "scanned": len(capped),
                },
            ))
        for child in capped:
            emit(child, origin, protected=False)

    scan_container(_managed_root(base), "managed_root")

    # 4) Explicit extras from the caller.
    for candidate in candidates or []:
        emit(Path(candidate), "explicit", protected=False)
    for root in container_roots or []:
        scan_container(Path(root), "explicit_root")

    return results, evidence


def inspect_prefix(
    path: Path | str,
    home: Path | None = None,
) -> tuple[dict | None, list[Evidence]]:
    """Inspect one prefix candidate in detail — strictly read-only."""
    base = Path(home) if home is not None else Path.home()
    absolute = Path(os.path.abspath(path))
    protected = absolute == base / ".wine"
    return _inspect_candidate(absolute, "inspect", protected)
