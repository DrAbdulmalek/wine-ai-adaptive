import shutil

from wine_ai.command import run_read_only
from wine_ai.evidence import Evidence, EvidenceStatus


def probe_vulkan():

    executable = shutil.which("vulkaninfo")

    if not executable:

        return (
            {"available": False},
            [
                Evidence(
                    "vulkan",
                    EvidenceStatus.UNPROVEN,
                    "vulkaninfo not found",
                    "shutil.which",
                )
            ],
        )

    result = run_read_only(
        [executable, "--summary"],
        timeout=12,
    )

    names = []

    for line in result.stdout.splitlines():

        if "deviceName" in line and "=" in line:

            names.append(
                line.split("=", 1)[1].strip()
            )

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
