# Decision record: prefix_hash redesign (machine-keyed HMAC) and the snapshot runtime contract

- **Date:** 2026-11-11
- **Status:** Accepted (implemented on `feature/deepseek-review-enhancements`, PR #4)
- **Deciders:** repository maintainer (DrAbdulmalek), acting on the
  independent ChatGPT security audit delivered on 2026-11-11;
  executed by the engineering agent.

## Context

The independent ChatGPT audit of the v4 consolidation round (recorded
in `docs/reviews/EXTERNAL_REVIEWS.md` §10) verified the commits and CI
runs on the PR head, then rejected one design claim and flagged two
gaps:

1. **`prefix_hash` was not identity-hiding.** The v4 round generated it
   as an *unsalted, truncated SHA-256* of the absolute prefix path
   (`hashlib.sha256(absolute).hexdigest()[:16]`). That prevents storing
   the raw path but does not hide identity: common Wine paths
   (`~/.wine`, `~/.steam/steam/steamapps/compatdata/…`) can be guessed,
   hashed offline and matched against stored values; the stable
   64-bit fingerprint links records across sessions; and nothing
   protects the value once it leaves the machine. The audit classified
   the claim "revealing nothing about directory names or users" /
   "privacy-preserving by construction" as **CONTRADICTED**, and
   recommended either deleting the column or redesigning it as an HMAC
   keyed by an independently protected local secret, documenting the
   protection limits, and never exporting it in share packages.
2. **The tests did not prove the privacy claims.** They proved the raw
   path is rejected and SQL CHECK constraints work, but not
   path-guessing resistance or cross-machine unlinkability.
3. **The runtime contract was ambiguous.** `detect_wine_version` runs
   `wine --version` via `subprocess` when no `WINE_VERSION`-style hint
   exists; describing a snapshot as "read-only" does not mean "executes
   no external program", and that had to be stated explicitly in the
   contract.

The audit also found the previous decision record was dated
2026-11-12 while its implementing commits and CI evidence are dated
2026-11-11; that date error is corrected together with this redesign.

## Decision

### D1 — `prefix_hash` is a machine-keyed HMAC, not a plain hash

`environment.snapshot.prefix_hash_of` now computes
`HMAC-SHA256(key, abspath)[:32]` — 32 lowercase hex characters
(128 bits). The key is derived as
`SHA-256("wine-ai-adaptive/prefix-hash/v1" || 0x00 || machine-id)` from
the first readable local machine identifier (`/etc/machine-id`, then
`/var/lib/dbus/machine-id`). The identifier is read-only, is never
stored and never leaves the machine.

- No schema change: the store's `CHECK` (`length BETWEEN 16 AND 64`,
  lowercase hex) already accepts the new shape; `SCHEMA_VERSION`
  stays 4. v4 never shipped outside this branch, so no data migration
  is required; any local dev database holding old 16-hex unsalted
  values keeps them (they are inert pseudonyms, not re-derivable), and
  new writes are 32-hex.
- **Fail-closed:** if no machine identifier is readable, no pseudonym
  is produced at all (`None`) — an unprotected fingerprint is never
  emitted.
- `key` is an explicit optional parameter for deterministic tests.

### D2 — Honest limits, not anonymity

The documented contract is now stated plainly (module docstring, store
docstring, README, THREAT_MODEL):

- `prefix_hash` is **not** anonymity. Same-machine cross-session
  correlation is the feature, by design.
- Any local process able to read the machine identifier can recompute
  the pseudonym — such a process could read the raw path anyway; the
  protection targets the value *leaving* the machine.
- Different machines never produce the same pseudonym for the same
  path: no cross-machine correlation.
- Guessing common paths offline is infeasible without the key
  (HMAC, 128-bit output).

### D3 — Never exported

`prefix_hash` was already outside the portability export allow-list
(`portability.EXPORT_FIELDS`); this round pins it with a regression
test (`test_prefix_hash_is_never_exported`), covering both the default
and the `include_prefix_id=True` opt-in. The column is local-only by
construction, not by convention.

### D4 — Explicit runtime contract for external programs

`detect_wine_version` and `collect` gained `allow_exec: bool = True`.
Default behavior is unchanged (one `wine --version` call, 5 s timeout,
only when no environment hint exists), but the contract is now
explicit in the module docstring, and `allow_exec=False` forbids any
subprocess (environment hints only). `wine_ai.environment.inventory`
keeps its own documented `wine --version` probe via `run_read_only`.

### D5 — Evidence dates must match commit dates

Decision records and review-session records must carry the date their
implementing commits were authored (2026-11-11 here). The erroneous
2026-11-12 stamps in the v3→v4 record filename/content and in
`EXTERNAL_REVIEWS.md` §9 were corrected to 2026-11-11.

## Consequences

- `PREFIX_HASH_CHARS` is 32; `EnvironmentSnapshot.prefix_hash` values
  are 128-bit HMAC truncations. Values remain opaque hex to the store,
  bridge and queries — no API change.
- Old 16-hex values in pre-redesign local dev databases are tolerated
  by the existing `CHECK`; they are not re-derivable and cannot be
  upgraded in place (the raw path is not kept). Operators who want a
  clean store can delete the column values; new writes are keyed.
- The audit's "Contradicted" claim is reclassified by the new tests:
  guessing resistance (`test_hash_is_hmac_not_plain_sha256`),
  machine-scoping (`test_hash_is_machine_scoped_not_cross_machine`),
  fail-closed behavior (`test_hash_fail_closed_without_local_key`) and
  export exclusion (`test_prefix_hash_is_never_exported`) are now
  machine-checked.

## Verification (this repository, this round)

- `python -m compileall -q src tests`: clean.
- `PYTHONPATH=src python -m unittest discover -s tests/unit -p 'test_*.py'`:
  **270 tests, all passing** (was 262; +3 exec-contract tests,
  +4 prefix-hash property tests, +1 export-exclusion test, 2 updated).
- Secret scan: clean. No new dependency (`hmac` is stdlib). No network.
- CI on the head of this round `b4e3aaab15ad`: PROVEN — run
  `43064711529` on GitHub Actions completed `success`
  (https://github.com/DrAbdulmalek/wine-ai-adaptive/actions/runs/43064711529) (Python 3.11/3.12/3.13, `pull_request` event;
  status and PR-head match verified via API). The documentation
  commit recording this line follows that run (docs-only, per
  the established round pattern).
