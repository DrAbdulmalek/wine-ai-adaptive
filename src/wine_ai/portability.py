"""Anonymized export/import of diagnostic records for wine-ai-adaptive.

Design summary
--------------
The export package is a stable JSON document. It contains only a strict
allow-list of fields from each diagnostic record. It never contains:

- raw logs or prompts
- Wine prefix contents
- full timestamps (year/month only, or nothing)
- ``prefix_id`` unless explicitly requested

Records are re-validated on the way OUT as well as on the way IN: before
any record is exported, it is checked against the store's own stored-row
rules (``validate_existing_record``). A database that was tampered with
through a raw SQLite connection fails the export instead of emitting
unstructured data.

The import path validates the package header (exact key set, typed
fields), then validates each record via the store's own
``_prepare_record``, then inserts the batch through
``KnowledgeStore.add_records_atomic``.

Residual risk (documented, not eliminated)
------------------------------------------
``add_records_atomic`` is per-batch atomic with ``MAX_BATCH_SIZE = 100``.
Import is chunked at that size to respect the store's limit. Validation
is all-or-nothing (every record passes ``_prepare_record`` before any
insert). If a chunk insert fails after validation (rare: disk or trigger
errors), earlier chunks remain inserted. Import is therefore NOT
all-or-nothing at the package level — only at the validation level.
This is the only residual risk of the portability layer and is
documented here and in docs/portability.md.

File handling
-------------
- ``write_package`` writes atomically (temp file in the target
  directory + ``os.replace``), refuses symbolic-link targets, and never
  creates directories. The export path itself is caller-supplied: the
  caller is responsible for choosing a safe location.
- Exported packages are written 0600 (owner-only); they contain
  diagnostic metadata even when anonymized.
- ``read_package`` requires a regular, non-symlink file, enforces a
  hard byte cap (``MAX_PACKAGE_BYTES``) before parsing, and decodes
  strict UTF-8.

Format versioning
-----------------
``format``           identifies the package kind.
``package_version``  gates backward-incompatible format changes.
``schema_version``   gates the underlying store schema.

All header fields are validated with an exact key-set allow-list; any
unknown, missing or mistyped header field fails closed.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wine_ai.knowledge.store import (
    MAX_BATCH_SIZE,
    SCHEMA_VERSION,
    KnowledgeStore,
    validate_existing_record,
)


FORMAT_ID = "wine-ai-adaptive.export"
PACKAGE_VERSION = 1
SUPPORTED_PACKAGE_VERSIONS = frozenset({1})

MAX_EXPORT_RECORDS = 1000
MAX_IMPORT_RECORDS = 1000

# Hard ceiling on the serialized package size accepted by read_package.
# 1,000 records at their maximum legal size stay far below this; the cap
# exists so a hostile file cannot exhaust memory before validation.
MAX_PACKAGE_BYTES = 4 * 1024 * 1024

# Exact header key set: missing or extra keys are refused.
HEADER_KEYS = frozenset({
    "format",
    "package_version",
    "schema_version",
    "exported_at",
    "record_count",
    "includes_prefix_id",
    "includes_time",
    "records",
})

# "YYYY-MM" with a real month ("unknown" is allowed for created_at_ym
# as a documented informational value).
_YEAR_MONTH_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")

EXPORT_FIELDS = (
    "wine_version",
    "distro",
    "error_category",
    "evidence_status",
    "confidence",
    "gpu_vendor",
    "error_code",
)

IMPORT_ALLOWED_KEYS = frozenset(EXPORT_FIELDS) | {"prefix_id", "created_at_ym"}


class PortabilityError(ValueError):
    """Raised for any package-shape or import-validation failure."""


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _year_month(value: Any) -> str:
    """Reduce a timestamp to ``YYYY-MM``.

    The value must parse as a real ISO-8601 datetime; anything else —
    wrong type, malformed, impossible month — yields the documented
    placeholder ``"unknown"`` instead of a silently truncated string.
    """
    if not isinstance(value, str):
        return "unknown"
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return "unknown"
    if len(value) < 7:
        return "unknown"
    return value[:7]


def _export_one(
    record: dict[str, Any],
    *,
    include_prefix_id: bool,
    include_time: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in EXPORT_FIELDS:
        if field not in record:
            continue
        value = record[field]
        if value is None:
            continue
        out[field] = value
    if include_prefix_id:
        value = record.get("prefix_id")
        if value is not None:
            out["prefix_id"] = value
    if include_time:
        out["created_at_ym"] = _year_month(record.get("created_at"))
    return out


def export_records(
    store: KnowledgeStore,
    *,
    include_prefix_id: bool = False,
    include_time: bool = True,
    max_records: int = MAX_EXPORT_RECORDS,
) -> dict[str, Any]:
    """Build an anonymized export package as a Python dict.

    Parameters
    ----------
    store:
        A ``KnowledgeStore`` instance.
    include_prefix_id:
        If ``False`` (default), ``prefix_id`` is never included.
    include_time:
        If ``True`` (default), include a ``created_at_ym`` field (``YYYY-MM``).
        If ``False``, omit timestamps entirely.
    max_records:
        Upper bound on exported records. Must be ``1..MAX_EXPORT_RECORDS``.
    """
    if not isinstance(include_prefix_id, bool):
        raise PortabilityError("include_prefix_id must be bool")
    if not isinstance(include_time, bool):
        raise PortabilityError("include_time must be bool")
    if (
        not isinstance(max_records, int)
        or isinstance(max_records, bool)
        or not 1 <= max_records <= MAX_EXPORT_RECORDS
    ):
        raise PortabilityError(
            f"max_records must be an int in 1..{MAX_EXPORT_RECORDS}"
        )

    raw_records = store.list_records(limit=max_records)
    records: list[dict[str, Any]] = []
    for raw in raw_records:
        # Fail-closed export gate: a tampered database cannot leak its
        # rows through the export path.
        try:
            validate_existing_record(raw)
        except ValueError as exc:
            raise PortabilityError(
                f"export blocked: stored record failed re-validation: {exc}"
            ) from exc
        records.append(
            _export_one(
                raw,
                include_prefix_id=include_prefix_id,
                include_time=include_time,
            )
        )

    return {
        "format": FORMAT_ID,
        "package_version": PACKAGE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m"),
        "record_count": len(records),
        "includes_prefix_id": include_prefix_id,
        "includes_time": include_time,
        "records": records,
    }


def write_package(package: dict[str, Any], path: str | Path) -> Path:
    """Write ``package`` to ``path`` as UTF-8 JSON.

    Safety properties:

    - the package must pass the full header validation first;
    - a symbolic link or non-regular file at ``path`` is refused
      instead of being followed or replaced;
    - the write is atomic: a temp file is created in the target
      directory (mode 0600), fully written and flushed, then moved
      into place with ``os.replace`` — readers never observe a
      partial file;
    - directories are never created; the caller owns the location.
    """
    validate_package(package)

    target = Path(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(info.st_mode):
            raise PortabilityError("export target is a symbolic link")
        if not stat.S_ISREG(info.st_mode):
            raise PortabilityError("export target is not a regular file")

    text = json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".wine-ai-export-",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(target))
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def export_to_file(
    store: KnowledgeStore,
    path: str | Path,
    *,
    include_prefix_id: bool = False,
    include_time: bool = True,
    max_records: int = MAX_EXPORT_RECORDS,
) -> Path:
    """Convenience: build a package and write it in one call."""
    package = export_records(
        store,
        include_prefix_id=include_prefix_id,
        include_time=include_time,
        max_records=max_records,
    )
    return write_package(package, path)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PortabilityError(msg)


def _validate_header(package: Any) -> dict[str, Any]:
    _require(isinstance(package, dict), "package must be a JSON object")
    _require(
        set(package) == HEADER_KEYS,
        "package header must contain exactly the documented fields "
        f"{sorted(HEADER_KEYS)}",
    )
    _require(
        package.get("format") == FORMAT_ID,
        f"format must be {FORMAT_ID!r}",
    )
    version = package.get("package_version")
    _require(
        isinstance(version, int)
        and not isinstance(version, bool)
        and version in SUPPORTED_PACKAGE_VERSIONS,
        f"unsupported package_version: {version!r}",
    )
    _require(
        package.get("schema_version") == SCHEMA_VERSION,
        f"schema_version must be {SCHEMA_VERSION}, "
        f"got {package.get('schema_version')!r}",
    )
    exported_at = package.get("exported_at")
    _require(
        isinstance(exported_at, str)
        and _YEAR_MONTH_PATTERN.fullmatch(exported_at) is not None,
        "exported_at must be a 'YYYY-MM' string with a real month",
    )
    _require(
        isinstance(package.get("includes_prefix_id"), bool),
        "includes_prefix_id must be bool",
    )
    _require(
        isinstance(package.get("includes_time"), bool),
        "includes_time must be bool",
    )
    _require(
        isinstance(package.get("records"), list),
        "records must be a list",
    )
    _require(
        isinstance(package.get("record_count"), int)
        and not isinstance(package.get("record_count"), bool),
        "record_count must be int",
    )
    _require(
        package["record_count"] == len(package["records"]),
        "record_count does not match len(records)",
    )
    _require(
        len(package["records"]) <= MAX_IMPORT_RECORDS,
        f"records exceed MAX_IMPORT_RECORDS={MAX_IMPORT_RECORDS}",
    )
    return package


def validate_package(package: Any) -> dict[str, Any]:
    """Public header validator. Useful for tests and callers holding a dict."""
    return _validate_header(package)


def read_package(path: str | Path) -> dict[str, Any]:
    """Read and header-validate an export package from disk.

    The path must be a regular, non-symlink file. The raw bytes are
    capped at ``MAX_PACKAGE_BYTES`` before parsing and must decode as
    strict UTF-8. ``FileNotFoundError`` propagates to the caller.
    """
    target = Path(path)
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise PortabilityError("package path is a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise PortabilityError("package path is not a regular file")

    with open(target, "rb") as handle:
        blob = handle.read(MAX_PACKAGE_BYTES + 1)
    if len(blob) > MAX_PACKAGE_BYTES:
        raise PortabilityError(
            f"package exceeds MAX_PACKAGE_BYTES={MAX_PACKAGE_BYTES}"
        )
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PortabilityError("package is not valid UTF-8") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PortabilityError(f"invalid JSON: {exc.msg}") from exc
    return _validate_header(data)


def _normalize_record(
    record: Any,
    *,
    includes_prefix_id: bool,
    index: int,
) -> dict[str, Any]:
    _require(
        isinstance(record, dict),
        f"records[{index}] must be a JSON object",
    )
    unknown = set(record) - IMPORT_ALLOWED_KEYS
    _require(
        not unknown,
        f"records[{index}] has unknown keys: {sorted(unknown)}",
    )
    if not includes_prefix_id:
        _require(
            "prefix_id" not in record,
            f"records[{index}] contains prefix_id but includes_prefix_id is false",
        )
    if "created_at_ym" in record:
        ym = record["created_at_ym"]
        _require(
            ym == "unknown"
            or (
                isinstance(ym, str)
                and _YEAR_MONTH_PATTERN.fullmatch(ym) is not None
            ),
            f"records[{index}].created_at_ym must be 'YYYY-MM' or 'unknown'",
        )
    normalized: dict[str, Any] = {}
    for field in EXPORT_FIELDS:
        if field in record:
            normalized[field] = record[field]
    if includes_prefix_id and "prefix_id" in record:
        normalized["prefix_id"] = record["prefix_id"]
    # created_at_ym is informational only (format-validated above);
    # created_at is regenerated by the store on insert.
    return normalized


def import_package(
    store: KnowledgeStore,
    package: Any,
) -> list[int]:
    """Validate ``package`` and insert its records into ``store``.

    Validation is all-or-nothing: every record passes the store's own
    ``_prepare_record`` before any insert. If any record is invalid,
    nothing is inserted and ``PortabilityError`` propagates.

    Insertion is chunked at ``MAX_BATCH_SIZE`` to respect the store's
    own batch limit. See the module docstring for the residual risk.
    """
    package = _validate_header(package)
    includes_prefix_id = package["includes_prefix_id"]
    records_in = package["records"]

    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(records_in):
        record = _normalize_record(
            raw,
            includes_prefix_id=includes_prefix_id,
            index=index,
        )
        try:
            # Strict validation gate: this is the exact rule the store
            # applies internally. Calling it here gives validation
            # atomicity across the whole package.
            store._prepare_record(record)  # noqa: SLF001
        except ValueError as exc:
            raise PortabilityError(
                f"records[{index}] rejected by store: {exc}"
            ) from exc
        prepared.append(record)

    if not prepared:
        return []

    inserted_ids: list[int] = []
    for offset in range(0, len(prepared), MAX_BATCH_SIZE):
        chunk = prepared[offset : offset + MAX_BATCH_SIZE]
        inserted_ids.extend(store.add_records_atomic(chunk))
    return inserted_ids


def import_package_file(
    store: KnowledgeStore,
    path: str | Path,
) -> list[int]:
    """Convenience: read from disk then import."""
    return import_package(store, read_package(path))


def import_package_lenient(
    store: KnowledgeStore,
    package: Any,
) -> dict[str, Any]:
    """Import with the documented downgrade policy for PROVEN records.

    Policy decision (2026-11-13, SECURITY_REMEDIATION_STATUS.md §2):

    - ``import_package`` stays the strict entry point: it rejects any
      package carrying PROVEN with ``PortabilityError``, all-or-nothing.
    - This lenient entry point never inserts a PROVEN row (the store
      write barrier forbids that). Historical PROVEN claims inside an
      incoming package are downgraded to UNPROVEN, and the ORIGINAL
      claim is preserved in the returned report, tied to the stored row
      via its index and inserted id. The caller must persist this
      report as the import log so no provenance semantics are silently
      lost.
    - Records without PROVEN are unaffected.

    The returned report::

        {
            "inserted_ids": [...],
            "record_count": <int>,
            "downgraded": [
                {
                    "index": <int, position in package["records"]>,
                    "inserted_id": <int, row id in the target store>,
                    "original_evidence_status": "PROVEN",
                    "final_evidence_status": "UNPROVEN",
                    "reason": "proven_write_barrier_import_policy",
                },
                ...
            ],
        }
    """
    package = _validate_header(package)
    includes_prefix_id = package["includes_prefix_id"]
    records_in = package["records"]

    prepared: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []
    for index, raw in enumerate(records_in):
        record = _normalize_record(
            raw,
            includes_prefix_id=includes_prefix_id,
            index=index,
        )
        if record.get("evidence_status") == "PROVEN":
            # Preserve the original claim in the report, then downgrade:
            # the store write barrier forbids new PROVEN rows, but the
            # incoming semantics must not vanish silently.
            record = dict(record)
            record["evidence_status"] = "UNPROVEN"
            downgraded.append({
                "index": index,
                "original_evidence_status": "PROVEN",
                "final_evidence_status": "UNPROVEN",
                "reason": "proven_write_barrier_import_policy",
            })
        try:
            store._prepare_record(record)  # noqa: SLF001
        except ValueError as exc:
            raise PortabilityError(
                f"records[{index}] rejected by store: {exc}"
            ) from exc
        prepared.append(record)

    if not prepared:
        return {"inserted_ids": [], "record_count": 0, "downgraded": downgraded}

    inserted_ids: list[int] = []
    for offset in range(0, len(prepared), MAX_BATCH_SIZE):
        chunk = prepared[offset : offset + MAX_BATCH_SIZE]
        inserted_ids.extend(store.add_records_atomic(chunk))

    # Link each preserved claim to the row id it became.
    for entry in downgraded:
        position = entry["index"]
        entry["inserted_id"] = inserted_ids[position] if position < len(inserted_ids) else None

    return {
        "inserted_ids": inserted_ids,
        "record_count": len(prepared),
        "downgraded": downgraded,
    }
