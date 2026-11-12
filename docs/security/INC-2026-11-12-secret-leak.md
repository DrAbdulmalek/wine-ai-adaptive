# INC-2026-11-12-01: Secret leak in chat session

**Status:** OPEN — this record stays incomplete until the owner
confirms revocation and rotation. Only the owner may change ⬜ to ✅.

## Severity

**SUSPECTED — severity deliberately NOT assigned yet.**

Binding owner decision (2026-11-13): this incident is recorded as
suspected. Neither SEV1 nor credential rotation may be declared
before evidence exists; only the owner may apply the ladder below
after verifying token status with the providers. The agent must not
upgrade the classification on its own authority.

Proposed classification ladder (DRAFT for owner adoption — the
incident's own class stays "suspected" until the owner applies it):

| Class | Severity |
|---|---|
| Owner-verified leak of a live secret to an external channel | SEV1 |
| Exposure with no evidence of abuse | SEV2 |
| Near miss (secret almost pasted/committed, avoided) | SEV3 |

Why "suspected" and not SEV1 today: the author of this record (an
agent) has no independent evidence of what exactly entered the chat
session, whether the credentials were live at that moment, or whether
the pasted strings were real values rather than placeholders. Only
the owner can verify this. Assigning SEV1 now would itself be an
unverified claim — the exact failure mode this round exists to
remove.

Required evidence before any classification or rotation claim (see
"Required evidence" below):

- what was pasted, where, and when (owner states);
- whether the tokens were live at paste time and now (owner checks
  GitHub security log / DeepSeek console);
- provider-side review results recorded in Impact analysis.

## Timeline (UTC)

Times are intentionally left blank: only the owner knows them, and this
record must not invent them.

| Time (UTC) | Event | Actor |
|---|---|---|
| TBD (owner) | Discovery — suspected secrets exposure in a chat session reported | owner |
| TBD (owner) | Owner states exactly what was pasted and where | owner |
| TBD (owner) | Revoke (GitHub PAT) — only after exposure is confirmed | owner |
| TBD (owner) | Revoke (DeepSeek key) — only after exposure is confirmed | owner |
| TBD (owner) | Rotate (new credentials issued) — only after exposure is confirmed | owner |
| 2026-11-13 | Incident record created (initially mis-classified SEV1); secret-scanning step added to CI | agent round |
| 2026-11-13 | Reclassified to SUSPECTED per binding owner decision: no SEV1 and no rotation claim before evidence (classification ladder demoted to a draft; severity assignment deferred to the owner) | agent round |

## Rotation ledger

Classification below is SUSPECTED-EXPOSED, not LEAKED: exposure is
alleged, not verified. Rows may only be marked ✅ after the owner
confirms both the exposure and the completed rotation.

| Service | Classification | Rotated? | By | Date |
|---|---|---|---|---|
| GitHub PAT (repo access) | SUSPECTED-EXPOSED | ⬜ | owner | — |
| DeepSeek API key | SUSPECTED-EXPOSED | ⬜ | owner | — |

No secret values are recorded here, ever. If a value is needed for
verification, it lives only in the provider's console, not in this
repository.

## Required evidence (blocks any severity upgrade or rotation claim)

All of the following must be verified by the owner before the
severity ladder is applied or any row above is marked ✅:

- [ ] Owner states exactly which credential values (if any) were
      pasted, into which chat/session, and when.
- [ ] Owner verifies token status at paste time and now: GitHub
      security log (PAT events, repos touched) and DeepSeek console
      (key list, usage/billing).
- [ ] GitHub audit log: check for unauthorised usage of the suspected
      PAT (auth events, repo/setting changes, unknown IPs) since
      first use.
- [ ] DeepSeek console: check usage/billing for calls not made by the
      owner since first use.
- [ ] Findings recorded here (counts + dates only; no tokens, no raw
      logs).

## Root corrective actions

- [x] Secret scanning in CI (`gitleaks` step in
      `.github/workflows/ci.yml`) — added in this round. Verified
      locally with gitleaks v8.28.0 on 2026-11-13: a realistic
      high-entropy AWS-shaped key is detected (rule `generic-api-key`,
      exit 1); the canonical AWS-documentation example key is
      allowlisted and is NOT detected, so the owner-side acceptance
      commit (on a scratch branch, deleted afterwards) must use a
      realistic-shaped dummy key. Key-shaped material is never quoted
      in committed files.
- [ ] Pre-commit secret-scan hook for local work (owner to adopt).
- [ ] Policy acknowledgement: never paste keys/tokens into chat
      sessions or documents; share provider names, not values
      (owner to acknowledge).
- [ ] Post-rotation: mark both ledger rows ✅ with dates, then close
      this incident.

## Lessons (recorded, no blame)

- Secrets may have reached a chat because the working session mixed
  credential handling with review work. Credential operations belong
  in the provider's console, isolated from assistant sessions.
- The repository itself was scanned: no key material was committed;
  the suspected leak channel was the chat session, not git history.
  Re-verify with `gitleaks detect --source .` after the CI step lands.
- Process lesson from this reclassification: an agent recording an
  incident must not assign the top severity on inference alone — the
  initial SEV1 classification in this very record was itself an
  unverified claim, and was demoted to SUSPECTED by the binding owner
  decision of 2026-11-13.
