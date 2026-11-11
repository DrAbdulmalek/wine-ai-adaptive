"""End-to-end example: from log text to structured, queryable knowledge.

Wires the whole offline pipeline together with a temporary database:

1. Extract structured error codes from diagnostic log text
   (no raw text is ever stored).
2. Enrich the batch with a read-only environment snapshot
   (abstract prefix name only).
3. Persist findings through the KnowledgeBridge atomically.
4. Query the store through the read-only query layer and the
   bundled GPU vendor knowledge.

Run it from the project root after an editable install:

    python examples/end_to_end.py
"""

from pathlib import Path
from tempfile import mkdtemp

from wine_ai.diagnostics.codes import extract_error_codes
from wine_ai.environment.snapshot import collect
from wine_ai.gpu_compatibility.vendor_data import (
    is_plausible_graphics_error,
    normalize_vendor,
    vendor_notes,
)
from wine_ai.knowledge.bridge import persist_findings
from wine_ai.knowledge.store import KnowledgeStore


SAMPLE_LOG = """
err:module:import_dll Library d3dcompiler_47.dll not found
wine: Unhandled exception 0xC0000005 at address 0x7b43bc3d
err:seh:call_exception_handler 0x8007007e
"""

SAMPLE_ENV = {
    "SteamAppId": "1245620",
    "STEAM_COMPAT_DATA_PATH": "/home/user/.local/share/Steam/steamapps/compatdata/1245620",
    "WINE_VERSION": "wine-9.0",
}


def main() -> None:
    # 1. Structured error codes: identifiers only, never raw log text.
    codes = extract_error_codes(SAMPLE_LOG)
    print("Extracted error codes:")
    for entry in codes:
        print(f"  {entry['kind']:8} {entry['code']:20} "
              f"category={entry['category']} confidence={entry['confidence']}")

    # 2. Read-only environment snapshot (abstract prefix name only).
    snapshot = collect(env=SAMPLE_ENV, prefix_path=SAMPLE_ENV["STEAM_COMPAT_DATA_PATH"])
    print(f"Environment: {snapshot.to_dict()}")

    # 3. Persist structured findings atomically.
    db_path = Path(mkdtemp(prefix="wine_ai_e2e_")) / "diagnostics.sqlite3"
    store = KnowledgeStore(db_path)

    vendor = normalize_vendor("AMD Radeon RX 6700")
    findings = [
        {
            "category": entry["category"] or "unknown",
            "status": "PARTIALLY_PROVEN",
            "confidence": entry["confidence"],
            "error_code": entry["code"],
        }
        for entry in codes
    ]

    record_ids = persist_findings(
        store,
        findings,
        wine_version=snapshot.wine_version,
        distro=snapshot.distro,
        gpu_vendor=vendor,
        prefix_id=snapshot.to_prefix_id(),
    )
    print(f"Persisted records: {record_ids}")

    # 4. Read-only query layer + bundled vendor knowledge.
    print("Records by category:", store.category_counts())
    print("Memory-related records:",
          [r["id"] for r in store.find_by_error_category("memory")])
    print("Recurring codes:", store.correlate_error_codes())

    for entry in codes:
        code = entry["code"]
        if entry["category"] == "graphics":
            plausible = is_plausible_graphics_error(vendor, code)
            print(f"{code}: plausible on {vendor}? {plausible}")

    print("Vendor notes:", vendor_notes(vendor))


if __name__ == "__main__":
    main()
