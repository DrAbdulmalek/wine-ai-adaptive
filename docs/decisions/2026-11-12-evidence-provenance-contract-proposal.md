# Evidence provenance contract proposal (P1)

**State:** PROPOSAL — NOT IMPLEMENTED, NOT APPROVED AS A TRUST POLICY  
**Date:** 2026-11-12  
**Applies to:** diagnostic evidence records and any future recommendation engine

This document defines a fail-closed engineering shape for provenance. It does not select or invent a trusted producer, certify any existing evidence, or enable recommendations. The trust anchor is an explicit owner decision and remains unresolved.

## 1. Current boundary

`KnowledgeStore` validates record structure and allowed vocabulary. Its `evidence_status` value is caller-supplied metadata, not proof that a source was authentic, that a claim follows from the source, or that the source is current. A SHA-256 digest alone proves only byte-for-byte correspondence to the bytes whose digest was recorded; it does not authenticate the publisher or establish that the evidence is true.

Until a trust anchor and deterministic verifier are approved and implemented, no record may become a user-facing **Recommendation** solely because its stored status says `PROVEN`. The safe default is no recommendation.

## 2. Proposed evidence envelope

A future versioned evidence envelope should carry, at minimum:

| Field | Meaning | Validation requirement |
|---|---|---|
| `evidence_id` | Stable identifier for this evidence item | Strict bounded syntax; unique within its declared producer namespace |
| `contract_version` | Provenance-envelope schema version | Exact supported version; unknown versions fail closed |
| `producer_id` | Identity of the evidence producer | Must resolve through an owner-approved trust-anchor registry; free-form strings are not trusted |
| `producer_version` | Exact producer release/build | Must be covered by the approved producer policy |
| `source_id` | Canonical source identifier | Must be allow-listed or independently authenticated under the approved policy |
| `source_revision` | Immutable source revision, publication ID, or content version | Must be sufficient to retrieve/recheck the source; mutable URLs alone are insufficient |
| `raw_evidence_sha256` | Digest of the exact raw evidence bytes | Recompute from the bytes; reject mismatch; digest is integrity metadata, not source authentication |
| `raw_evidence_ref` | Local reference to retained evidence bytes | Local-only, bounded, no arbitrary filesystem paths; missing bytes means verification is blocked |
| `claim` | Structured diagnostic claim | Closed schema and bounded categorical fields; no raw log text or arbitrary commands |
| `derivation_id` | Identifier for the deterministic rule or verifier | Must resolve to a versioned, locally available derivation implementation |
| `derivation_version` | Exact derivation version | Exact match; no silent substitution with latest |
| `derived_result` | Structured output produced from the evidence | Must be independently recomputed, not trusted as supplied |
| `observed_at` / `expires_at` | Evidence observation and validity interval | Strict timestamp parsing; expired evidence cannot authorize a current recommendation |
| `parent_evidence_ids` | Provenance dependencies | Every parent must resolve and verify; cycles and missing parents fail closed |

The exact field names and storage layout are proposals. The owner must approve the trust policy before implementation.

## 3. Trust-anchor decision (blocking)

Before a verifier can produce a trusted result, the owner must select and document:

1. Which producers are trusted (for example, a project-maintained signed ruleset, an upstream vendor advisory, or a locally captured diagnostic source).
2. How producer identity is authenticated (for example, pinned signing keys or a separately maintained allow-list with an auditable update process).
3. How key rotation, revocation, compromise, and historical signature validation work.
4. Whether source bytes are retained locally, and the retention/size/privacy limits.
5. What freshness policy applies to mutable upstream advisories.
6. Which claims can be deterministically derived, and which require human review.

No producer, key, signature scheme, or trust level is selected by this proposal. A repository commit or a hash alone must not be treated as proof of upstream authenticity.

## 4. Verifier contract

A future `verify_evidence(envelope, local_store, trust_policy)` implementation should:

1. Validate the envelope against one exact supported contract version.
2. Resolve `producer_id` using the approved trust-anchor registry; unknown, revoked, or out-of-policy producers return `BLOCKED`.
3. Retrieve the exact raw bytes from the local evidence store. Missing, oversized, malformed, or unavailable bytes return `BLOCKED`; no cloud fallback is permitted.
4. Recompute SHA-256 and compare it with `raw_evidence_sha256`. A mismatch returns `CONTRADICTED` or `BLOCKED` according to the approved policy; it must never be accepted.
5. Authenticate source provenance using the approved signature/attestation mechanism. A matching hash without authenticated producer identity is insufficient.
6. Resolve the exact derivation implementation/version and recompute the structured claim from raw evidence.
7. Compare recomputed output with the supplied structured claim; mismatch returns `CONTRADICTED`.
8. Verify all parent evidence recursively, reject cycles, missing parents, and broken links.
9. Apply freshness/revocation policy and return a machine-readable result with reason codes and verifier version.
10. Perform no network access, filesystem writes, Wine execution, prefix modification, or external command execution.

The verifier must be deterministic for the same evidence bytes, trust-policy snapshot, and verifier version.

## 5. Recommendation gate

The recommendation layer must consume verifier output, not caller-supplied status fields:

- `PROVEN`: eligible for a Recommendation only if every provenance, authenticity, derivation, freshness, and policy check passes.
- `PARTIALLY_PROVEN`: may be shown only as a clearly labeled Lead, never as a confirmed fix.
- `UNPROVEN` or `BLOCKED`: excluded from recommendations.
- `CONTRADICTED`: suppress the affected claim/rule group and expose a contradiction reason; do not silently fall back to weaker evidence.

If there are no eligible `PROVEN` items, the engine must return no recommendations. It must not manufacture a generic fix to fill an empty result.

## 6. Required negative and mutation tests

The implementation is not acceptable without tests covering at least:

- Raw evidence changed after its digest was recorded.
- A digest copied from a different evidence item.
- Unknown, forged, revoked, or out-of-policy producer identity.
- Invalid or missing signature/attestation.
- Missing raw evidence bytes or unavailable local reference.
- Unknown contract, producer, or derivation version.
- Broken parent reference and provenance-chain cycle.
- Recomputed claim differs from the stored claim.
- Expired evidence and revoked producer key.
- Duplicate IDs across producer namespaces.
- Malformed/oversized evidence and path traversal attempts.
- No-network/no-cloud-fallback behavior.
- Caller sets `evidence_status=PROVEN` while provenance is absent: recommendation must remain blocked.
- Mutation test: remove or bypass at least one verifier check and demonstrate that the relevant test suite fails.

Tests must use synthetic fixtures and temporary directories only. No real medical records, Wine prefixes, API keys, or private logs may enter CI.

## 7. Implementation sequence and release gate

1. Owner approves the trust-anchor policy and source classes.
2. Define a versioned envelope and local raw-evidence retention model.
3. Implement the verifier behind a disabled feature flag or non-authorizing diagnostic CLI.
4. Add negative, adversarial, and mutation tests.
5. Independently review the trust policy and verifier implementation.
6. Only then connect verifier results to recommendations.
7. Release under a new version; do not rewrite the published `v1.0.0` tag.

**Current decision:** all recommendation authorization remains **BLOCKED** pending steps 1–5. This proposal does not change runtime behavior.
