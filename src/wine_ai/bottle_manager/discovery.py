"""Read-only, symlink-resistant discovery of Wine prefixes.

This module never executes Wine, creates files, or modifies a prefix.
Filesystem traversal is performed one directory component at a time with
O_DIRECTORY|O_NOFOLLOW and directory file descriptors. This prevents a
symlink in any ancestor component from redirecting inspection.

The remaining limits are explicit: a privileged actor able to replace
mounts or mutate the same directory concurrently can still affect the
filesystem namespace. The module fails closed when an opened object no
longer matches its expected type.
"""
from __future__ import annotations

import errno
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

MAX_SCAN_SUBDIRS = 64
REG_HEADER_BYTES = 64

MARKER_DIR = "drive_c"
MARKER_FILES = ("system.reg", "user.reg", "userdef.reg")
_ARCH_LINE = re.compile(r"^#{1,2}\s*arch\s*-\s*win(32|64)\s*$")
_MANAGED_ROOT_PARTS = (".local", "share", "wine-ai-adaptive", "prefixes")


class _UnsafePathError(OSError):
    """A symlink or unexpected object was found in a directory path."""


def _directory_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise _UnsafePathError(errno.ENOTSUP, "safe directory flags unavailable")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_dir_path(path: Path | str) -> int:
    """Open an absolute directory without following any path component."""
    absolute = os.path.abspath(os.fspath(path))
    flags = _directory_flags()
    fd = os.open("/", flags)
    try:
        for component in Path(absolute).parts[1:]:
            try:
                info = os.stat(component, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                raise
            if stat.S_ISLNK(info.st_mode):
                raise _UnsafePathError(
                    errno.ELOOP, "symlink in directory path", absolute
                )
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(
                    errno.ENOTDIR, "non-directory path component", component
                )
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except OSError as exc:
                # A component may have been exchanged after stat(). O_NOFOLLOW
                # makes the open fail rather than traversing the replacement.
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise _UnsafePathError(
                        exc.errno, "directory component changed during open",
                        component,
                    ) from exc
                raise
            opened = os.fstat(next_fd)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(next_fd)
                raise _UnsafePathError(
                    errno.ENOTDIR, "opened object is not a directory", component
                )
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        fd = _open_dir_path(path)
    except OSError:
        return None
    try:
        return _identity(fd)
    finally:
        os.close(fd)


def _stat_at(directory_fd: int, name: str):
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def _read_reg_arch(directory_fd: int) -> tuple[str | None, str | None]:
    """Read only a regular system.reg file; FIFO opens are non-blocking."""
    try:
        info = _stat_at(directory_fd, "system.reg")
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if stat.S_ISLNK(info.st_mode):
        return None, "Symlinked system.reg rejected"
    if not stat.S_ISREG(info.st_mode):
        return None, "system.reg is not a regular file"

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open("system.reg", flags, dir_fd=directory_fd)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            return None, "system.reg changed type before it could be read"
        raw = os.read(fd, REG_HEADER_BYTES)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        os.close(fd)

    first_line = raw.decode("utf-8", errors="replace").split("\n", 1)[0]
    match = _ARCH_LINE.match(first_line.strip())
    if not match:
        return None, None
    return f"win{match.group(1)}", None


def _probe_nested_directory(
    directory_fd: int, parts: tuple[str, ...]
) -> tuple[bool, str | None]:
    """Probe a nested directory using only no-follow dirfd operations."""
    current_fd = os.dup(directory_fd)
    try:
        for component in parts:
            try:
                info = _stat_at(current_fd, component)
            except FileNotFoundError:
                return False, None
            except OSError as exc:
                return False, f"{type(exc).__name__}: {exc}"
            if stat.S_ISLNK(info.st_mode):
                return False, f"Symlinked directory rejected: {component}"
            if not stat.S_ISDIR(info.st_mode):
                return False, f"Non-directory component rejected: {component}"
            try:
                next_fd = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=current_fd,
                )
            except OSError as exc:
                return False, f"{type(exc).__name__}: {exc}"
            opened = os.fstat(next_fd)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(next_fd)
                return False, f"Opened object is not a directory: {component}"
            os.close(current_fd)
            current_fd = next_fd
        return True, None
    finally:
        os.close(current_fd)


def _inspect_candidate(
    path: Path,
    origin: str,
    protected: bool,
    protected_identity: tuple[int, int] | None = None,
) -> tuple[dict | None, list[Evidence]]:
    evidence: list[Evidence] = []
    source = f"bottle_manager.discovery:{origin}"
    absolute = Path(os.path.abspath(path))

    try:
        directory_fd = _open_dir_path(absolute)
    except _UnsafePathError as exc:
        evidence.append(Evidence(
            "prefix_candidate", EvidenceStatus.BLOCKED,
            "Symlinked prefix candidate or unsafe path component rejected; traversal refused",
            source, {"path": str(absolute), "error": str(exc)},
        ))
        return None, evidence
    except PermissionError as exc:
        evidence.append(Evidence(
            "prefix_candidate", EvidenceStatus.BLOCKED,
            "Candidate directory could not be examined",
            source, {"path": str(absolute), "error": str(exc)},
        ))
        return None, evidence
    except OSError as exc:
        status = (
            EvidenceStatus.UNPROVEN
            if exc.errno in (errno.ENOENT, errno.ENOTDIR)
            else EvidenceStatus.BLOCKED
        )
        evidence.append(Evidence(
            "prefix_candidate", status,
            "Candidate path is absent or could not be opened safely",
            source, {"path": str(absolute), "error": str(exc)},
        ))
        return None, evidence

    try:
        identity = _identity(directory_fd)
        is_protected = (
            protected
            or (protected_identity is not None and identity == protected_identity)
        )

        missing: list[str] = []
        rejected_links: list[str] = []
        wrong_types: list[str] = []
        for name in (MARKER_DIR, *MARKER_FILES):
            try:
                marker_info = _stat_at(directory_fd, name)
            except FileNotFoundError:
                missing.append(name)
                continue
            except OSError as exc:
                evidence.append(Evidence(
                    "prefix_markers", EvidenceStatus.BLOCKED,
                    "Prefix marker could not be examined safely",
                    source, {"path": str(absolute / name), "error": str(exc)},
                ))
                return None, evidence
            if stat.S_ISLNK(marker_info.st_mode):
                rejected_links.append(name)
            elif name == MARKER_DIR and not stat.S_ISDIR(marker_info.st_mode):
                wrong_types.append(name)
            elif name in MARKER_FILES and not stat.S_ISREG(marker_info.st_mode):
                wrong_types.append(name)

        if rejected_links or wrong_types:
            evidence.append(Evidence(
                "prefix_markers", EvidenceStatus.BLOCKED,
                "Symlinked registry files or non-regular prefix markers rejected",
                source,
                {
                    "path": str(absolute),
                    "rejected_symlinks": rejected_links,
                    "wrong_types": wrong_types,
                },
            ))
            return None, evidence
        if missing:
            evidence.append(Evidence(
                "prefix_markers", EvidenceStatus.UNPROVEN,
                "Path does not look like a Wine prefix (missing markers)",
                source, {"path": str(absolute), "missing": missing},
            ))
            return None, evidence

        architecture, arch_error = _read_reg_arch(directory_fd)
        if arch_error:
            evidence.append(Evidence(
                "prefix_architecture", EvidenceStatus.BLOCKED,
                "Registry header could not be read safely",
                source,
                {"path": str(absolute / "system.reg"), "error": arch_error},
            ))
            return None, evidence
        elif architecture:
            evidence.append(Evidence(
                "prefix_architecture", EvidenceStatus.PROVEN,
                f"Architecture {architecture} from system.reg header",
                source, {"path": str(absolute / "system.reg")},
            ))
        else:
            evidence.append(Evidence(
                "prefix_architecture", EvidenceStatus.UNPROVEN,
                "Architecture header unparsable; left unknown",
                source, {"path": str(absolute / "system.reg")},
            ))

        syswow64_present, nested_error = _probe_nested_directory(
            directory_fd, ("drive_c", "windows", "syswow64")
        )
        if nested_error:
            evidence.append(Evidence(
                "prefix_architecture", EvidenceStatus.BLOCKED,
                "Nested Windows architecture path could not be trusted",
                source, {"path": str(absolute / MARKER_DIR / "windows" / "syswow64"),
                         "error": nested_error},
            ))

        arch_conflict = architecture == "win32" and syswow64_present
        if arch_conflict:
            evidence.append(Evidence(
                "prefix_architecture", EvidenceStatus.CONTRADICTED,
                "Registry header says win32 but syswow64 exists "
                "(conflicting signals kept, not guessed)",
                source, {"path": str(absolute)},
            ))

        return {
            "name": absolute.name,
            "path": str(absolute),
            "origin": origin,
            "protected": is_protected,
            "architecture": architecture,
            "arch_conflict": arch_conflict,
            "syswow64_present": syswow64_present,
            "markers": [MARKER_DIR, *MARKER_FILES],
            "_identity": identity,
        }, evidence
    finally:
        os.close(directory_fd)


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
    """Discover Wine prefixes without executing commands or writing files."""
    base = Path(home) if home is not None else Path.home()
    protected_path = Path(os.path.abspath(base / ".wine"))
    protected_identity = _directory_identity(protected_path)
    evidence: list[Evidence] = []
    results: list[dict] = []
    seen: dict[tuple[int, int], dict] = {}

    def emit(candidate: Path, origin: str, protected: bool = False) -> None:
        absolute = Path(os.path.abspath(candidate))
        info, item_evidence = _inspect_candidate(
            absolute,
            origin,
            protected=(protected or absolute == protected_path),
            protected_identity=protected_identity,
        )
        evidence.extend(item_evidence)
        if info is None:
            return
        identity = info.pop("_identity")
        if identity in seen:
            # Never downgrade a protected identity discovered via another source.
            existing = seen[identity]
            existing["protected"] = existing["protected"] or info["protected"]
            return
        seen[identity] = info
        info["_identity"] = identity
        results.append(info)

    declared = os.environ.get("WINEPREFIX", "").strip()
    if declared:
        emit(Path(declared), "WINEPREFIX")
    else:
        evidence.append(Evidence(
            "prefix_discovery", EvidenceStatus.UNPROVEN,
            "WINEPREFIX is not declared in the environment",
            "bottle_manager.discovery:WINEPREFIX",
        ))

    emit(protected_path, "default_prefix", protected=True)

    def scan_container(root: Path, origin: str) -> None:
        try:
            root_fd = _open_dir_path(root)
        except FileNotFoundError:
            evidence.append(Evidence(
                "prefix_discovery", EvidenceStatus.UNPROVEN,
                "Container root absent",
                f"bottle_manager.discovery:{origin}", {"path": str(root)},
            ))
            return
        except OSError as exc:
            evidence.append(Evidence(
                "prefix_discovery", EvidenceStatus.BLOCKED,
                "Container root could not be opened without following links",
                f"bottle_manager.discovery:{origin}",
                {"path": str(root), "error": str(exc)},
            ))
            return

        try:
            names: list[str] = []
            overflow = False
            try:
                with os.scandir(root_fd) as entries:
                    for entry in entries:
                        if len(names) == MAX_SCAN_SUBDIRS:
                            overflow = True
                            break
                        names.append(entry.name)
            except OSError as exc:
                evidence.append(Evidence(
                    "prefix_discovery", EvidenceStatus.BLOCKED,
                    "Container root could not be listed",
                    f"bottle_manager.discovery:{origin}",
                    {"path": str(root), "error": str(exc)},
                ))
                return

            for name in sorted(names):
                emit(root / name, origin)
            if overflow:
                evidence.append(Evidence(
                    "prefix_discovery", EvidenceStatus.PARTIALLY_PROVEN,
                    "Scan capped at MAX_SCAN_SUBDIRS entries; remaining entries were not enumerated",
                    f"bottle_manager.discovery:{origin}",
                    {
                        "path": str(root),
                        "scanned": len(names),
                        "limit": MAX_SCAN_SUBDIRS,
                        "additional_entries_not_enumerated": True,
                    },
                ))
        finally:
            os.close(root_fd)

    scan_container(_managed_root(base), "managed_root")
    for candidate in candidates or []:
        emit(Path(candidate), "explicit")
    for root in container_roots or []:
        scan_container(Path(root), "explicit_root")

    for item in results:
        item.pop("_identity", None)
    return results, evidence


def inspect_prefix(
    path: Path | str,
    home: Path | None = None,
) -> tuple[dict | None, list[Evidence]]:
    """Inspect one prefix without following symlinks in any path component."""
    base = Path(home) if home is not None else Path.home()
    protected_path = Path(os.path.abspath(base / ".wine"))
    info, evidence = _inspect_candidate(
        Path(path),
        "inspect",
        protected=Path(os.path.abspath(path)) == protected_path,
        protected_identity=_directory_identity(protected_path),
    )
    if info is not None:
        info.pop("_identity", None)
    return info, evidence
