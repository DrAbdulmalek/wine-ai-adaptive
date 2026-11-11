
# Portability — anonymized export / import

## Why

Sharing diagnostic knowledge across users or communities should never leak:

- full timestamps (only `YYYY-MM` at most, or nothing)
- Wine prefix IDs (`prefix_id`) by default
- raw logs (never stored, by design)
- private paths or system identifiers

## Format (stable, versioned)

```json
{
  "format": "wine-ai-adaptive.export",
  "package_version": 1,
  "schema_version": 2,
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

`format` + `package_version` + `schema_version` are validated on import.
Any mismatch fails closed.

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

## Import

```python
from wine_ai.portability import import_package_file
ids = import_package_file(store, "export.json")
```

## Boundaries

- `prefix_id` is **opt-in** (`include_prefix_id=True`). Default: excluded.
- Timestamps reduced to `YYYY-MM` by default; `include_time=False` omits them.
- Every record is validated via the store's own `_prepare_record`
  **before any insert**. If any record is invalid, nothing is inserted
  (validation atomicity).
- Insertion is chunked at `MAX_BATCH_SIZE = 100`. If a chunk insert fails
  after validation (rare: disk or trigger errors), earlier chunks remain.
  This is the documented residual risk.
- Both export and import are capped at 1,000 records per package
  (matching `list_records` cap). For larger sets, split into multiple packages.
- **No CLI wiring yet.** The library API is complete; a follow-up PR can
  add `wine-ai export` / `wine-ai import` once the current `cli.py` source
  is verified.
```

---
