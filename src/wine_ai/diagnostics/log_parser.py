import os
import stat
from pathlib import Path
import re

from wine_ai.evidence import Evidence, EvidenceStatus


MAX_LOG_SIZE = 2 * 1024 * 1024

PATTERNS = {
    "missing_module": re.compile(
        r"err:module:import_dll|Library .* not found",
        re.IGNORECASE,
    ),
    "access_violation": re.compile(
        r"c0000005|access violation",
        re.IGNORECASE,
    ),
    "graphics_or_direct3d": re.compile(
        r"d3d11|dxgi|vulkan|opengl|d3d12",
        re.IGNORECASE,
    ),
    "rpc_or_com": re.compile(
        r"ole|rpc|com server",
        re.IGNORECASE,
    ),
}


def inspect_log_with_content(path: str):
    """Read once through a no-follow descriptor and return parsed metadata and text."""
    log_path = Path(path).expanduser()
    display_name = log_path.name or "log"
    fd = None
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise OSError("O_NOFOLLOW is unavailable")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(log_path, flags)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Not a regular file")
        if before.st_size > MAX_LOG_SIZE:
            raise ValueError(f"Log exceeds {MAX_LOG_SIZE} bytes")
        chunks = []
        remaining = MAX_LOG_SIZE + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_LOG_SIZE:
            raise ValueError(f"Log exceeds {MAX_LOG_SIZE} bytes")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError("Log identity changed while open")
        before_signature = (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns
        )
        after_signature = (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        )
        if before_signature != after_signature or after.st_size != len(raw):
            raise OSError("Log changed while it was being read")
        content = raw.decode("utf-8", errors="replace")
        size = len(raw)
    except (OSError, ValueError) as exc:
        return (
            {"path": display_name, "read_error": (
                f"{type(exc).__name__} (errno={exc.errno})"
                if isinstance(exc, OSError) else str(exc)
            )},
            [Evidence("log_read", EvidenceStatus.BLOCKED,
                      "Log could not be safely read", f"log:{display_name}",
                      {"error": (
                          f"{type(exc).__name__} (errno={exc.errno})"
                          if isinstance(exc, OSError) else str(exc)
                      )})],
            None,
        )
    finally:
        if fd is not None:
            os.close(fd)

    matches = {name: len(pattern.findall(content)) for name, pattern in PATTERNS.items()}
    found = {name: count for name, count in matches.items() if count > 0}
    status = EvidenceStatus.PARTIALLY_PROVEN if found else EvidenceStatus.UNPROVEN
    data = {"path": display_name, "size_bytes": size, "matches": matches,
            "classification": list(found)}
    evidence = [Evidence(
        "log_patterns", status,
        "Pattern matches are heuristic indicators, not root-cause proof",
        f"log:{display_name}", {"matches": matches},
    )]
    return data, evidence, content


def inspect_log(path: str):
    """Compatibility wrapper: inspect a log without returning its raw text."""
    data, evidence, _content = inspect_log_with_content(path)
    return data, evidence
