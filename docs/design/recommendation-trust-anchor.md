# Recommendation trust anchor — design constraints (P1, deferred)

**Status:** DESIGN ONLY — no recommendation engine code may be written
until the owner answers the open question in §4.
**Date:** 2026-11-13
**Complements:**
- `docs/SECURITY_REMEDIATION_STATUS.md` §2 (evidence provenance boundary)
- `docs/decisions/2026-11-12-evidence-provenance-contract-proposal.md`
  (earlier proposal; this document adds the trust-anchor requirement
  that proposal lacked)

## 0. Why this document exists

The unified remediation plan (2026-11-13, §3) deferred the
recommendation engine with one blocking condition: a real trust anchor
must be defined and decided before any engine code exists. This
document states what "trusted verification" must mean as a TESTABLE
contract, enumerates the negative cases the future verifier must fail
closed on, and leaves exactly one decision to the owner.

**Binding owner decision (2026-11-13):** the deferral is TOTAL — no
command, no contract stub, no partial engine, no interface. The
interim `wine_ai.recommendation` stub added earlier in this round was
removed in the same round; nothing executable remains. This document
is DESIGN ONLY and must not be read as an existing capability.

## 1. Definition of "trusted verification" (testable contract)

A recommendation engine may rely on a `PROVEN` record **only** when all
of the following hold. Each clause is testable; nothing here may be
relaxed silently.

1. **Evidence provenance envelope.** Every `PROVEN` record carries an
   `EvidenceProvenance` object with exactly five mandatory fields:

   | Field | Type / format | Meaning |
   |---|---|---|
   | `producer_id` | non-empty identifier from the anchor's allow-list | who produced the evidence |
   | `contract_version` | non-empty version string | which provenance-contract version the record was produced under |
   | `raw_evidence_hash` | lowercase SHA-256 hex (64 chars) | hash of the raw evidence artifact the claim was derived from |
   | `derivation_method` | identifier from a registered, reviewed set | the deterministic procedure that maps raw evidence → claim |
   | `produced_at` | ISO-8601 UTC | when the evidence was produced (freshness input) |

2. **The verifier re-derives; it never trusts the stored field.**
   `verifier.py` must recompute the claim from the raw evidence (the
   artifact addressed by `raw_evidence_hash`, retained locally) using
   `derivation_method`, and compare. A stored `evidence_status` value,
   digest, or repository-local metadata is NEVER sufficient on its own
   (the same rule as the store's containment barrier, §2 of the
   remediation status).

3. **A real trust anchor.** `known_producers` alone is NOT a trust
   anchor: a list of names proves nothing about who actually produced a
   row. The anchor must be one of (owner decides, §4):
   - a public key / key set (e.g. embedded Ed25519 public keys) with a
     documented rotation story, or
   - a signature chain (e.g. GPG-signed evidence manifests), or
   - a signed evidence source with a pinned verification key.
   The verifier checks the producer's signature over the raw evidence
   (or its hash) against the anchor before anything else.

4. **Policy decision recorded.** Freshness windows, revocation
   handling, and the behaviour on anchor compromise must be written
   down (in this document or a successor ADR) BEFORE engine code.

5. **Determinism.** Two verifier runs over the same inputs must agree;
   no network access, no clocks in the comparison path (timestamps are
   inputs to policy, not to validity of a derivation).

## 2. What the engine may and may not do

- May read `PROVEN` rows that pass the full contract above.
- May NOT write `PROVEN` rows (the storage-layer barrier stays; see
  `docs/SECURITY_REMEDIATION_STATUS.md` §2 — the barrier is containment,
  not authentication).
- May NOT elevate `UNPROVEN`/`PARTIALLY_PROVEN` rows into
  recommendations, even if a signature verifies, until the contract
  above is fully implemented.
- Must surface `NO_PROVEN_EVIDENCE` as an explicit empty-result
  status when no row qualifies — never guess. (No stub or interface
  exists today: the engine is fully deferred until §4 is answered.)

## 3. Future verifier tests (specification, not implementation)

At minimum, seven negative cases must fail closed. These are the
acceptance names for the future `tests/unit/test_evidence_verifier.py`:

| # | Case | Input mutation | Required outcome |
|---|---|---|---|
| 1 | `missing_provenance` | `PROVEN` row without an `EvidenceProvenance` envelope | reject |
| 2 | `unknown_producer` | `producer_id` not in the anchor's allow-list | reject |
| 3 | `hash_mismatch` | `raw_evidence_hash` ≠ SHA-256 of the retained artifact | reject |
| 4 | `unknown_derivation` | `derivation_method` not in the registered set | reject |
| 5 | `derivation_false` | re-derivation over the raw evidence yields a different claim | reject |
| 6 | `tampered_record` | any field edited after signing | reject (signature/consistency check fails) |
| 7 | `no_trust_anchor` | verifier constructed with an empty/unconfigured anchor | reject everything, report `NO_PROVEN_EVIDENCE` |

Positive case: one producer whose signature verifies, hash matches, and
re-derivation reproduces the claim → exactly that row becomes
recommendation-eligible.

Mutation discipline: the test suite must be written so that removing
any single verifier check (e.g. skipping the signature check) flips at
least one named case from reject to accept.

## 4. OPEN QUESTION — owner decision required (blocks all engine code)

**Which trust anchor does the owner authorise?**

- (a) Embedded Ed25519 public key(s) + signed evidence manifests
      (offline-friendly, no external dependency);
- (b) GPG-signed evidence producer(s) (reuses existing tooling, heavier
      operational story);
- (c) Another anchor the owner names.

Until this question is answered: no `feature/evidence-gated-recommendations`
branch, no verifier code, no engine. `known_producers` alone remains
explicitly insufficient.

## 5. Out of scope for this round

- Key generation, distribution, and rotation tooling.
- Raw-evidence retention format and size policy.
- Freshness/revocation semantics beyond "must be decided before code".
