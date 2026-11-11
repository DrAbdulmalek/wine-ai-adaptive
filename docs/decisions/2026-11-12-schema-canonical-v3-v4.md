# Decision record: canonical store schema (v3 → v4) and the fate of the v0.3.1 package

- **Date:** 2026-11-12
- **Status:** Accepted (implemented on `feature/deepseek-review-enhancements`, PR #4)
- **Deciders:** repository maintainer (DrAbdulmalek) via the
  "add and develop" directive chain; executed by the engineering agent.

## Context

Two parallel store designs emerged during the external-review sessions:

1. **The published line (PR #4, schema v3)** — `error_code` +
   `prefix_id` columns, table-rebuild migration inside a single
   `BEGIN IMMEDIATE` transaction, full stored-SQL fingerprint match,
   read-only query layer, and an export gate
   (`src/wine_ai/portability.py`). Tested, documented, published.
2. **The unpublished "v0.3.1" package (external AI package)** — schema
   v2 with `launcher` + `prefix_hash` columns, comma-normalization
   fingerprint fix, `error_category` bridge key, CLI status/diagnose.

The independent consolidation review (ChatGPT session, shared as
`chatgpt.com/share/6af49172-f4b8-83ed-8b49-1ef4dc9d224e` transcript and
the DeepSeek attachment `chat-New chat.txt`) proved that merging both
lines sequentially would leave two incompatible migrations of the same
table, and recommended:

> **v3 is the source of truth. The v0.3.1 `store.py` is SUPERSEDED and
> must not be merged. Its unit value (CLI, parser fixes, bridge key,
> the privacy-motivated `launcher`/`prefix_hash` design) is ported
> unit-wise on top of the stack, after diffing against the actual
> modules.**

The maintainer then forwarded the package again with the standing
directive "أضف ما يلي أيضًا وطوّره" (add the following too and develop
it) — read as approval to port the unit value, including the
`launcher`/`prefix_hash` pair the review had left behind an explicit
gate ("if maintainer approves: schema v4 via the SAME rebuild pattern
as v3").

## Decisions

### D1 — Canonical schema advances v3 → v4, not back to v2

`SCHEMA_VERSION = 4`. The v4 canonical table is the v3 table plus two
optional `CHECK`-covered columns:

- `launcher TEXT` — `CHECK (launcher IS NULL OR launcher IN
  ('lutris','bottles','heroic','steam','wine','unknown'))`. An
  allow-listed categorical label; never a path, never a version
  string.
- `prefix_hash TEXT` — `CHECK (prefix_hash IS NULL OR (length BETWEEN
  16 AND 64 AND lowercase hex))`. A truncated SHA-256 pseudonym of the
  absolute prefix path produced by `environment.snapshot.prefix_hash_of`.
  The raw path is never accepted or stored.

Rationale: privacy-preserving cross-session correlation (per the
package's design intent) without weakening any v3 guarantee.

### D2 — v3 → v4 migration copies the v2 → v3 rebuild pattern exactly

`_migrate_v3_to_v4` runs inside one `BEGIN IMMEDIATE` transaction;
fingerprint (stored SQL == canonical v3 statement after whitespace
normalization) and column-set pre-checks run inside the same
transaction; rows are copied with `NULL` for the new columns; the
backup table is dropped and the version stamped — all or nothing.
`_migrate_v2_to_v3` now rebuilds into the canonical **v3** statement
(previously it reused the then-canonical constant, which became v4's
text — a latent defect this round fixed by introducing explicit
`EXPECTED_TABLE_SQL_V3` / `EXPECTED_COLUMNS_V3` constants). Chains
v1→v2→v3→v4 and v2→v3→v4 run automatically on open.

### D3 — v0.3.1 items adopted (unit-value ports)

| v0.3.1 item | Disposition |
|---|---|
| Bridge key fix (`error_category` wins over `category`) | Adopted in `knowledge/bridge.py` with precedence and a normalization-to-`unknown` contract; covered by tests |
| `launcher` batch/finding parameter, allow-list validated | Adopted; per-finding override supported |
| `prefix_hash` batch/finding parameter, `^[a-f0-9]{16,64}$` | Adopted; raw paths rejected |
| Snapshot `prefix_hash` (truncated SHA-256, 16 hex) | Adopted in `environment/snapshot.py` (`prefix_hash_of`, `to_prefix_hash`) |
| Bare-Wine launcher value (`WINEPREFIX` set, no launcher marker → `"wine"`) | Adopted (`BARE_WINE_LAUNCHER`, `detect_launcher(..., prefix_declared=)`) |
| Regression suite (reopen-fresh, id-continuity, fail-closed legacy rows, `launcher` CHECK enforcement) | Adopted as `tests/unit/test_store_v4_migration.py` against the v3→v4 chain |
| Query by launcher | Adopted as `KnowledgeStore.find_by_launcher` |

### D4 — v0.3.1 items NOT adopted (recorded reasons)

| v0.3.1 item | Reason |
|---|---|
| Its `store.py` as a whole (schema v2 with `launcher`/`prefix_hash` instead of `prefix_id`) | Superseded by D1/D2; two competing migrations of one table would break user databases — the exact outcome the consolidation review forbade |
| `ntstatus>=1.1` as an optional dependency (`pyproject [project.optional-dependencies]`) | The repository iron rule is standard library only, with no optional external dependencies (owner rule, recorded in earlier sessions); the internal `diagnostics/codes.py` tables already provide the fallback layer the package reserved for `ntstatus` |
| `pytest` as dev dependency / `pytest.ini_options` in `pyproject.toml` | Tests run with `unittest` (stdlib-only, CI-consistent) |
| Generic 8-hex fallback in the parser (capture any `0x[0-9A-Fa-f]{8}` as low-confidence) | The repository parser deliberately restricts bare `0x` codes to known error-shape prefixes because Wine logs are saturated with raw return addresses (`0x7b…`); documented in `diagnostics/codes.py` and enforced by `test_error_codes.py` |
| CLI `diagnose` writing findings into a temporary store | The repository CLI is strictly read-only by design ("no database is created"); persisting belongs to the explicit bridge API, not the triage command |
| `find_by_error_code` length cap change (64 → 70) | Irrelevant after v4: `normalize_error_code` + the SQL CHECK already define the canonical code shape |
| Export of `launcher` in portability packages | Left out to keep `package_version` 1 stable; `launcher`/`prefix_hash` stay local-only this round (`prefix_hash` is a pseudonym of a local path and must never leave the machine) |

### D5 — Discovery protection precedence (P1-D contract, from the ChatGPT audit)

`discover_prefixes` dedup now upgrades protection instead of dropping
the re-emission: re-discovering the same path through any source can
never lower `protected` (the concrete regression was `$WINEPREFIX`
pointing at `~/.wine`, which previously recorded `protected=False`).
Origin keeps recording the first discovery source. Covered by
`test_bottle_discovery.ProtectionPrecedenceTests`; the empty-bottle
regression recommended by the same audit is covered by
`EmptyCandidateTests`.

## Consequences

- Old exports (`"schema_version": 3`) fail import fail-closed, as
  documented in `docs/portability.md` — by design.
- `list_records` / queries now return `launcher` and `prefix_hash`
  (both `None` for migrated rows).
- The next schema change must extend
  `EXPECTED_TABLE_SQL`/`EXPECTED_COLUMNS` and add a v4→v5 rebuild
  migration following the same pattern; version-specific constants
  (`EXPECTED_TABLE_SQL_V2`, `_V3`) exist precisely so each migration
  fingerprints the statement its databases actually carry.
- Race-condition and audit-concurrency tests recommended for the
  Create Bottle feature (PR #9 branch) remain pending on that branch
  and are out of scope here (documented in
  `docs/reviews/EXTERNAL_REVIEWS.md` §9).

## Verification (this repository, this round)

- `python -m compileall -q src tests`: clean.
- `PYTHONPATH=src python -m unittest discover -s tests/unit -p
  'test_*.py'`: **262 tests, all passing** (10 migration/v4, 8 bridge,
  9 snapshot, 5 discovery tests added or extended, plus three
  schema-shape contract assertions updated).
- Secret scan: clean.
- No subprocess added; no network; no new dependency; no API removal —
  existing callers keep working (231 pre-existing tests unchanged in
  behavior).
