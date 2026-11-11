"""Anonymized export/import of diagnostic records for wine-ai-adaptive.

Design summary
--------------
The export package is a stable JSON document. It contains only a strict
allow-list of fields from each diagnostic record. It never contains:

- raw logs or prompts
- Wine prefix contents
- full timestamps (year/month only, or nothing)
- ``prefix_id`` unless explicitly requested

The import path validates the package header, then validates each record
via the store's own ``_prepare_record``, then inserts the batch through
``KnowledgeStore.add_records_atomic``.

Residual risk (documented, not eliminated)
------------------------------------------
``add_records_atomic`` is per-batch atomic with ``MAX_BATCH_SIZE = 100``.
Import is chunked at that size to respect the store's limit. Validation
is all-or-nothing (every record passes ``_prepare_record`` before any
insert). If a chunk insert fails after validation (rare: disk or trigger
errors), earlier chunks remain inserted. This is the only residual risk
of the portability layer and is documented here and in docs/portability.md.

Format versioning
-----------------
``format``           identifies the package kind.
``package_version``  gates backward-incompatible format changes.
``schema_version``   gates the underlying store schema.

Both must match a supported value or import fails closed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wine_ai.knowledge.store import (
    MAX_BATCH_SIZE,
    SCHEMA_VERSION,
    KnowledgeStore,
)


FORMAT_ID = "wine-ai-adaptive.export"
PACKAGE_VERSION = 1
SUPPORTED_PACKAGE_VERSIONS = frozenset({1})

MAX_EXPORT_RECORDS = 1000
MAX_IMPORT_RECORDS = 1000

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
    if not isinstance(value, str) or len(value) < 7:
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
    records: list[dict[str, Any]] = [
        _export_one(
            raw,
            include_prefix_id=include_prefix_id,
            include_time=include_time,
        )
        for raw in raw_records
    ]

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
    """Write ``package`` to ``path`` as UTF-8 JSON. Does not create directories."""
    target = Path(path)
    text = json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(text + "\n", encoding="utf-8")
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
        package.get("format") == FORMAT_ID,
        f"format must be {FORMAT_ID!r}",
    )
    _require(
        package.get("package_version") in SUPPORTED_PACKAGE_VERSIONS,
        f"unsupported package_version: {package.get('package_version')!r}",
    )
    _require(
        package.get("schema_version") == SCHEMA_VERSION,
        f"schema_version must be {SCHEMA_VERSION}, "
        f"got {package.get('schema_version')!r}",
    )
    _require(
        isinstance(package.get("exported_at"), str),
        "exported_at must be a string",
    )
    _require(
        isinstance(package.get("includes_prefix_id"), bool),
        "includes_prefix_id must be bool",
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
    """Read and header-validate an export package from disk."""
    text = Path(path).read_text(encoding="utf-8")
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
    normalized: dict[str, Any] = {}
    for field in EXPORT_FIELDS:
        if field in record:
            normalized[field] = record[field]
    if includes_prefix_id and "prefix_id" in record:
        normalized["prefix_id"] = record["prefix_id"]
    # created_at_ym is informational only; created_at is regenerated by the store.
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
