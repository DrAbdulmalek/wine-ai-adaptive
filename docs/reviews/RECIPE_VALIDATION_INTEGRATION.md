# Recipe validation integration repair

Status: implementation committed; automated CI evidence pending.

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
- Validate evidence status and confidence before producing a finding.
  `BLOCKED` remains non-persistable; `PROVEN` can only be supplied as
  an independently asserted test outcome, never by recipe metadata.
- Convert the recipe tests to the project's stdlib `unittest` runner and
  exercise the actual `recipe_finding -> persist_findings -> KnowledgeStore`
  path using a temporary database.
- Include the Trados and memoQ JSON files in package data and verify that
  both are present in the built wheel in CI.

## Safety boundary

This layer remains data-only. It does not create, initialize, delete, or
execute Wine bottles; it does not invoke Wine, winetricks, subprocesses,
or external network services. Tests use temporary directories only.

## Evidence

The source changes and test cases are committed. No passing test or CI
result is claimed in this document until a corresponding GitHub Actions
run is observed for the final commit.
