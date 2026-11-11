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


def inspect_log(path: str):

    log_path = Path(path).expanduser()

    try:
        if not log_path.is_file():
            raise FileNotFoundError("Not a regular file")

        size = log_path.stat().st_size

        if size > MAX_LOG_SIZE:
            raise ValueError(
                f"Log exceeds {MAX_LOG_SIZE} bytes"
            )

        content = log_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    except (OSError, ValueError) as exc:

        return (
            {
                "path": str(log_path),
                "read_error": str(exc),
            },
            [
                Evidence(
                    "log_read",
                    EvidenceStatus.BLOCKED,
                    "Log could not be safely read",
                    str(log_path),
                    {"error": str(exc)},
                )
            ],
        )

    matches = {
        name: len(pattern.findall(content))
        for name, pattern in PATTERNS.items()
    }

    found = {
        name: count
        for name, count in matches.items()
        if count > 0
    }

    status = (
        EvidenceStatus.PARTIALLY_PROVEN
        if found
        else EvidenceStatus.UNPROVEN
    )

    data = {
        "path": str(log_path),
        "size_bytes": size,
        "matches": matches,
        "classification": list(found),
    }

    evidence = [
        Evidence(
            "log_patterns",
            status,
            "Pattern matches are heuristic indicators, not root-cause proof",
            str(log_path),
            {"matches": matches},
        )
    ]

    return data, evidence
