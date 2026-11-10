from wine_ai.knowledge.store import (
    ERROR_CATEGORIES,
    EVIDENCE_STATUSES,
    CONFIDENCE_LEVELS,
    GPU_VENDORS,
    MAX_BATCH_SIZE,
)


def persist_findings(
    store,
    findings,
    *,
    wine_version,
    distro,
    gpu_vendor="unknown",
):
    """
    Explicitly persist structured diagnostic findings atomically.

    Only approved fields are read and persisted.
    Raw logs, matched text, paths, prompts and extra fields
    are never passed to the database.
    """

    if not isinstance(findings, list):
        raise TypeError("findings must be a list")

    if len(findings) > MAX_BATCH_SIZE:
        raise ValueError("Too many findings in one batch")

    if not isinstance(gpu_vendor, str) or gpu_vendor not in GPU_VENDORS:
        raise ValueError("Unsupported gpu_vendor")

    records = []

    # Validate and prepare the entire batch before database access.
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Each finding must be a dictionary")

        category = finding.get("category", "unknown")
        if not isinstance(category, str):
            raise ValueError("Invalid finding category")

        if category not in ERROR_CATEGORIES:
            category = "unknown"

        status = finding.get(
            "status",
            "PARTIALLY_PROVEN",
        )
        if not isinstance(status, str) or status not in EVIDENCE_STATUSES:
            raise ValueError("Unsupported evidence status")

        confidence = finding.get("confidence", "low")
        if (
            not isinstance(confidence, str)
            or confidence not in CONFIDENCE_LEVELS
        ):
            raise ValueError("Unsupported confidence")

        # Construct a clean record explicitly.
        # Extra finding fields are intentionally discarded.
        records.append(
            {
                "wine_version": wine_version,
                "distro": distro,
                "error_category": category,
                "evidence_status": status,
                "confidence": confidence,
                "gpu_vendor": gpu_vendor,
            }
        )

    # The store validates all records before writing and commits
    # the batch as one transaction.
    return store.add_records_atomic(records)
