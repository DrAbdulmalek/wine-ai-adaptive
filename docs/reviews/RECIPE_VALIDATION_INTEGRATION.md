# Recipe validation integration repair

Status: implementation committed; CI evidence must be checked against the final branch head.

## Scope

This repair branch is based on
`security/p2-create-bottle-concurrency-validation` plus the original
`feature/bottle-manager-v2` recipe-layer commit. The original v2 branch
is preserved unchanged.

## Changes

- Treat `last_tested: null` as the explicit "not tested" state while
  validating non-null dates strictly as real `YYYY-MM-DD` calendar dates.
- Reject duplicate JSON object keys at every nesting level.
- Normalize recipe finding identifiers to the KnowledgeStore's
  `UPPER_SNAKE_CASE` error-code format and bound recipe names so the
  generated identifier fits the store's 64-character limit.
- `recipe_finding()` rejects `PROVEN` unconditionally.
- `record_test_outcome()` validates structured observation metadata but
  also rejects `PROVEN`: metadata and a caller-supplied reference do not
  establish report existence, authenticity, or correspondence to the
  claimed test.
- The generic `persist_findings()` bridge rejects `PROVEN` records.
  The low-level KnowledgeStore remains a caller-trusted storage API and
  is not an evidence-verification authority.
- Convert the recipe tests to the project's stdlib `unittest` runner and
  exercise the actual `recipe_finding -> persist_findings -> KnowledgeStore`
  path using a temporary database.
- Correct the evidence model: no recipe API or generic bridge may mint
  `PROVEN` until a trusted verifier, durable evidence binding, and
  independently reviewable trust configuration exist.
- Include the Trados and memoQ JSON files in package data and verify that
  both are present in the built wheel in CI.

## Safety boundary

This layer remains data-only. It does not create, initialize, delete, or
execute Wine bottles; it does not invoke Wine, winetricks, subprocesses,
or external network services. Tests use temporary directories only.

## Evidence

All tests use temporary directories and databases. No Wine, winetricks,
subprocess, real prefix, or sudo operation is involved. CI status is
reported only after a run is observed for the exact final branch head.
