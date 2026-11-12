# INC-2026-11-12-01: Secret leak in chat session

**Status:** OPEN — this record stays incomplete until the owner
confirms revocation and rotation. Only the owner may change ⬜ to ✅.

## Severity

**SEV1 (confirmed disclosure).**

Classification ladder used by this project:

| Class | Severity |
|---|---|
| Confirmed leak of a live secret to an external channel | SEV1 |
| Exposure with no evidence of abuse | SEV2 |
| Near miss (secret almost pasted/committed, avoided) | SEV3 |

Rationale: live credentials (a GitHub personal access token and a
DeepSeek API key) were pasted into an external chat session during the
2026-11-12 work round. Pasting into an external service is a confirmed
disclosure, so SEV1 applies regardless of whether abuse is later found.

## Timeline (UTC)

Times are intentionally left blank: only the owner knows them, and this
record must not invent them.

| Time (UTC) | Event | Actor |
|---|---|---|
| TBD (owner) | Discovery — secrets found pasted in a chat session | owner |
| TBD (owner) | Revoke (GitHub PAT) | owner |
| TBD (owner) | Revoke (DeepSeek key) | owner |
| TBD (owner) | Rotate (new credentials issued) | owner |
| 2026-11-13 | Incident record created; secret-scanning step added to CI | agent round |

## Rotation ledger

| Service | Classification | Rotated? | By | Date |
|---|---|---|---|---|
| GitHub PAT (repo access) | LEAKED | ⬜ | owner | — |
| DeepSeek API key | LEAKED | ⬜ | owner | — |

No secret values are recorded here, ever. If a value is needed for
verification, it lives only in the provider's console, not in this
repository.

## Impact analysis

Pending owner review:

- [ ] GitHub audit log: check for unauthorised usage of the leaked PAT
      (auth events, repo/setting changes, unknown IPs) since first use.
- [ ] DeepSeek console: check usage/billing for calls not made by the
      owner since first use.
- [ ] Record findings here (counts + dates only; no tokens, no raw
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

- Secrets reached a chat because the working session mixed credential
  handling with review work. Credential operations belong in the
  provider's console, isolated from assistant sessions.
- The repository itself was scanned: no key material was committed;
  the leak channel was the chat session, not git history. Re-verify
  with `gitleaks detect --source .` after the CI step lands.
