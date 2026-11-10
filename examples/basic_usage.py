"""Basic usage example for the hardened KnowledgeStore.

Run it from the project root after an editable install:

    python examples/basic_usage.py
"""

from pathlib import Path
from tempfile import mkdtemp

from wine_ai.knowledge.store import KnowledgeStore


def main() -> None:
    # The database file is created with owner-only permissions (0600).
    db_dir = Path(mkdtemp(prefix="wine_ai_demo_"))
    db_path = db_dir / "diagnostics.sqlite3"
    store = KnowledgeStore(db_path)
    print(f"Database ready at: {db_path}")

    # Insert one structured record.
    record_id = store.add_record(
        wine_version="11.19",
        distro="Manjaro",
        error_category="graphics",
        evidence_status="PARTIALLY_PROVEN",
        confidence="low",
        gpu_vendor="amd",
    )
    print(f"Inserted record #{record_id}")

    # Insert an atomic batch: either every record lands, or none does.
    batch_ids = store.add_records_atomic(
        [
            {
                "wine_version": "10.15",
                "distro": "Arch Linux",
                "error_category": "missing_module",
                "evidence_status": "UNPROVEN",
                "confidence": "medium",
                "gpu_vendor": "nvidia",
            },
            {
                "wine_version": "9.0",
                "distro": "Debian",
                "error_category": "startup",
                "evidence_status": "BLOCKED",
                "confidence": "high",
            },
        ]
    )
    print(f"Inserted batch with ids: {batch_ids}")

    # Read the most recent records back.
    for record in store.list_records(limit=10):
        print(record)


if __name__ == "__main__":
    main()
