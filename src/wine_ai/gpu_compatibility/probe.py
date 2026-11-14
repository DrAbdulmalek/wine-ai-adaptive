import re
import shutil

from wine_ai.command import run_read_only
from wine_ai.evidence import Evidence, EvidenceStatus

# PCI vendor IDs of the three GPU vendors the knowledge store allows,
# as printed by `vulkaninfo --summary`. Matching is done on the exact
# lowercase ID; anything else maps to "unknown". This is a read-only
# lookup table: no network, no guessing beyond the listed IDs.
VENDOR_ID_MAP = {
    "0x8086": "intel",
    "0x1002": "amd",
    "0x10de": "nvidia",
}

_VENDOR_ID_RE = re.compile(r"^\s*vendorID\s*=\s*(0x[0-9A-Fa-f]{4,8})\s*$")
_DEVICE_NAME_RE = re.compile(r"^\s*deviceName\s*=\s*(.+?)\s*$")

MAX_DEVICES = 10


def parse_vulkan_summary(text):
    """Parse `vulkaninfo --summary` output into structured device data.

    Pure function: no subprocess, no filesystem access. Returns a dict
    with ``device_names`` (capped at ``MAX_DEVICES``), the parallel
    ``vendors`` list derived from the preceding ``vendorID`` line, and
    ``primary_vendor`` (the vendor of the first device, or
    "unknown"). Device names are truncated to 128 characters and are
    the only free-form values kept — no log text is retained.
    """
    names = []
    vendors = []
    current_vendor_id = None

    for line in text.splitlines():
        vendor_match = _VENDOR_ID_RE.match(line)
        if vendor_match:
            current_vendor_id = vendor_match.group(1).lower()
            continue

        name_match = _DEVICE_NAME_RE.match(line)
        if name_match:
            names.append(name_match.group(1)[:128])
            vendors.append(
                VENDOR_ID_MAP.get(current_vendor_id or "", "unknown")
            )
            current_vendor_id = None
            if len(names) >= MAX_DEVICES:
                break

    return {
        "device_names": names,
        "vendors": vendors,
        "primary_vendor": vendors[0] if vendors else "unknown",
    }


def probe_vulkan():

    executable = shutil.which("vulkaninfo")

    if not executable:

        return (
            {
                "available": False,
                "device_names": [],
                "vendors": [],
                "primary_vendor": "unknown",
            },
            [
                Evidence(
                    "vulkan",
                    EvidenceStatus.UNPROVEN,
                    "vulkaninfo not found",
                    "shutil.which",
                )
            ],
        )

    # run_read_only performs its own allow-listed PATH resolution. Keep
    # argv[0] as the bare command; absolute paths are intentionally rejected.
    result = run_read_only(
        ["vulkaninfo", "--summary"],
        timeout=12,
    )

    parsed = parse_vulkan_summary(result.stdout or "")
    names = parsed["device_names"]

    available = (
        result.returncode == 0
        and bool(names)
    )

    status = (
        EvidenceStatus.PROVEN
        if available
        else EvidenceStatus.PARTIALLY_PROVEN
    )

    data = {
        "available": available,
        "device_names": names,
        "vendors": parsed["vendors"],
        "primary_vendor": (
            parsed["primary_vendor"] if available else "unknown"
        ),
        "returncode": result.returncode,
        "error": result.error,
    }

    evidence = [
        Evidence(
            "vulkan_devices",
            status,
            f"Detected {len(names)} Vulkan device(s)",
            "vulkaninfo --summary",
            {
                "device_names": names,
                "stderr": result.stderr[:2000],
            },
        )
    ]

    return data, evidence
