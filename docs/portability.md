
# Portability — anonymized export / import

## Why

Sharing diagnostic knowledge across users or communities should reduce
metadata exposure to exactly what the documented fields and options
allow. It must never include:

- full timestamps (only `YYYY-MM` at most, `unknown` when the source
  value cannot be parsed, or nothing)
- Wine prefix IDs (`prefix_id`) by default
- raw logs (never stored, by design)
- private paths or system identifiers

## Format (stable, versioned)

```json
{
  "format": "wine-ai-adaptive.export",
  "package_version": 1,
  "schema_version": 3,
  "exported_at": "2026-11",
  "record_count": 2,
  "includes_prefix_id": false,
  "includes_time": true,
  "records": [
    {
      "wine_version": "11.19",
      "distro": "Manjaro",
      "error_category": "memory",
      "evidence_status": "PROVEN",
      "confidence": "high",
      "gpu_vendor": "amd",
      "error_code": "0xC0000005",
      "created_at_ym": "2026-11"
    },
    {
      "wine_version": "11.19",
      "distro": "Manjaro",
      "error_category": "missing_module",
      "evidence_status": "PARTIALLY_PROVEN",
      "confidence": "medium",
      "gpu_vendor": "intel",
      "error_code": "STATUS_DLL_NOT_FOUND",
      "created_at_ym": "2026-11"
    }
  ]
}
```

The header is validated against an **exact key set** — a package with
any missing or extra header field is refused. `package_version` must be
a real integer (a boolean does not pass), `exported_at` and
`created_at_ym` must be `YYYY-MM` with a real month (`unknown` is
accepted only for `created_at_ym`), and `includes_prefix_id` /
`includes_time` must be booleans. `schema_version` must match the
current store schema exactly; packages exported by older releases fail
closed.

## Export

```python
from wine_ai.knowledge.store import KnowledgeStore
from wine_ai.portability import export_to_file

store = KnowledgeStore("/path/to/knowledge.sqlite3")
export_to_file(store, "export.json")            # defaults
export_to_file(store, "export.json",
               include_prefix_id=True,          # opt-in
               include_time=False)              # no timestamps at all
```

Every record read from the database is **re-validated before it leaves
the store** (`validate_existing_record`). A database tampered with
through a raw SQLite connection fails the export with an error instead
of emitting unstructured data.

## Import

```python
from wine_ai.portability import import_package_file
ids = import_package_file(store, "export.json")
```

## File safety

- **Writing** is atomic: a temp file is created in the target directory
  (mode 0600), fully written, then moved into place with `os.replace`.
  A symbolic link or non-regular file at the target path is refused.
  Directories are never created — **the export path is caller-supplied;
  choosing a safe location is the caller's responsibility** (enforce
  `O_NOFOLLOW`-style checks again if you ever wire this to a CLI that
  accepts untrusted paths).
- **Reading** requires a regular, non-symlink file, enforces a hard
  byte cap (`MAX_PACKAGE_BYTES = 4 MiB`) before parsing, and decodes
  strict UTF-8.

## Boundaries

- `prefix_id` is **opt-in** (`include_prefix_id=True`). Default: excluded.
- Timestamps reduced to `YYYY-MM` (or `unknown`) by default;
  `include_time=False` omits them.
- Every record is validated via the store's own `_prepare_record`
  **before any insert**. If any record is invalid, nothing is inserted
  (validation atomicity).
- Insertion is chunked at `MAX_BATCH_SIZE = 100` and is atomic **per
  batch, not per package**: if a chunk insert fails after validation
  (rare: disk or trigger errors), earlier chunks remain. This is the
  documented residual risk and is pinned by a test.
- Both export and import are capped at 1,000 records per package
  (matching `list_records` cap). For larger sets, split into multiple
  packages.
- **No CLI wiring yet.** The library API is complete; a follow-up PR can
  add `wine-ai export` / `wine-ai import` once the current `cli.py`
  source is verified.


---
