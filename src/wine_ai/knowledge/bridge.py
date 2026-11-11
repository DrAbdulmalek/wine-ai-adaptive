from wine_ai.knowledge.store import (
    ERROR_CATEGORIES,
    EVIDENCE_STATUSES,
    CONFIDENCE_LEVELS,
    GPU_VENDORS,
    MAX_BATCH_SIZE,
    SAFE_LABEL,
    normalize_error_code,
)


def _validate_error_code(value):
    if value is None:
        return None
    return normalize_error_code(value)


def _validate_prefix_id(value):
    if value is None:
        return None
    if not isinstance(value, str) or not SAFE_LABEL.fullmatch(value):
        raise ValueError("Invalid prefix_id")
    return value


def persist_findings(
    store,
    findings,
    *,
    wine_version,
    distro,
    gpu_vendor="unknown",
    error_code=None,
    prefix_id=None,
):
    """
    Explicitly persist structured diagnostic findings atomically.

    Only approved fields are read and persisted.
    Raw logs, matched text, paths, prompts and extra fields
    are never passed to the database.

    ``error_code`` and ``prefix_id`` are optional structured values
    applied to every finding in the batch unless a finding carries its
    own ``error_code``/``prefix_id`` entry. Both must be allow-listed
    identifiers; arbitrary text is rejected.
    """

    if not isinstance(findings, list):
        raise TypeError("findings must be a list")

    if len(findings) > MAX_BATCH_SIZE:
        raise ValueError("Too many findings in one batch")

    if not isinstance(gpu_vendor, str) or gpu_vendor not in GPU_VENDORS:
        raise ValueError("Unsupported gpu_vendor")

    error_code = _validate_error_code(error_code)
    prefix_id = _validate_prefix_id(prefix_id)

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

        finding_error_code = _validate_error_code(
            finding.get("error_code", error_code)
        )
        finding_prefix_id = _validate_prefix_id(
            finding.get("prefix_id", prefix_id)
        )

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
                "error_code": finding_error_code,
                "prefix_id": finding_prefix_id,
            }
        )

    # The store validates all records before writing and commits
    # the batch as one transaction.
    return store.add_records_atomic(records)
