# Security remediation status and release gates

**Date:** 2026-11-12  
**Applies to:** current security-hardening PR and CAT-tool compatibility claims  
**Policy:** fail closed; no claim is promoted without reproducible evidence.

## Executive disposition

| Finding | Current disposition | What closes it |
|---|---|---|
| Same-UID Registry namespace race around final replacement | **OPEN — RELEASE BLOCKER** | A separately isolated broker/security boundary with a narrow authenticated protocol, independent review, and adversarial race tests. In-process flock, inode rechecks, and os.replace do not close the final same-UID check-to-rename interval. |
| Caller-supplied PROVEN in KnowledgeStore | **MITIGATED at public write API; provenance verifier still absent** | Public add_record / add_records_atomic now reject new PROVEN writes. Historical rows remain readable. A future trusted verifier and approved trust-anchor policy are still required before any recommendation may rely on PROVEN. |
| Create Bottle disabled | **INTENTIONALLY BLOCKED** | Do not remove the gate until the same-UID threat is addressed by a distinct OS security boundary, the create protocol is independently reviewed, and negative/adversarial tests pass. BLOCKED is the correct safe behavior, not an outstanding implementation defect to bypass. |
| Trados Studio / memoQ compatibility | **UNPROVEN** | Per-version tests using legitimately licensed installers, isolated disposable prefixes, recorded Wine/build/OS/GPU details, and reproducible pass/fail evidence. Recipe presence or successful package CI is not application compatibility evidence. |
| Actual Manjaro install/runtime validation | **NOT RUN — RELEASE BLOCKER for a Manjaro compatibility claim** | Run the documented smoke and integration matrix on a clean supported Manjaro host/VM. Capture sanitized command output and package versions; do not upload license material, translation files, raw user logs, or prefix contents. |

<a id="النقطة-1"></a>

## 1. Registry same-UID race (النقطة 1)

The current registry uses directory descriptors, no-follow opens, advisory locking, unique temporary files, fsync, and identity revalidation. These controls protect against many path substitution and crash-consistency failures, but they do not create a compare-and-swap operation on a directory entry. A hostile process running under the same UID can race the final validation and rename.

**Do not describe this as fixed.** Do not rely on flock as a boundary against a non-cooperating same-UID process. The safe current disposition is to keep all Bottle-creation/write paths disabled.

A future design must use a genuinely separate security principal (not merely another process under the same UID), a minimal authenticated IPC contract, strict path/name allow-lists, descriptor-relative operations inside the broker, explicit ownership/mode policy, bounded requests, and an audit trail that excludes absolute home paths and sensitive content. The broker must not execute Wine or shell commands. Require independent review and race/fault-injection tests before enabling writes.

### 1.1 Isolation-level decision (2026-11-13)

Decision matrix, agreed during the patch review round. Recorded before any further code so no limited technical improvement is promoted into an unproven security claim.

| Level | What it closes | What stays open | Cost | Decision |
|---|---|---|---|---|
| L1: Linux `O_TMPFILE` (+ `linkat`/`renameat2` publication) | Unnamed temporary file cannot be preempted or swapped by name before publication | same-UID race on the final publication window; concurrent writers remain last-writer-wins (check-then-publish is not CAS) | low | **Adopted as a hardening layer only** — never described as closing same-UID |
| L2: Registry directory owned by a different system user (0700) | same-UID writes at the directory level | requires pre-provisioning (user creation, permissions), breaks self-contained install | medium | **Rejected** (installation complexity, not portable) |
| L3: Isolated broker + authenticated IPC | same-UID + concurrency + local tampering | logical TOCTOU inside the broker; needs its own review | high (systemd/root setup) | **Deferred to v2** with independent security review |

**Decision:** adopt L1 as an independent hardening improvement with honest documentation that same-UID remains an OPEN boundary in portable POSIX; keep the current identity checks and their documented limits; make no CAS claim against a hostile same-UID process.

**Rejected patch designs (review round 2026-11-13, do not re-apply as-is):**

- A RENAME_EXCHANGE-based "compare then rollback" publication: the comparison happens after the exchange, so it is not an atomic precondition; a rollback after a concurrent write can move or delete another writer's content via the temporary name; `os.write()` needs partial-write handling; renameat2 error mapping does not match the FileExistsError handling of the first path; the lock file lacks ownership/type/mode checks; fixed probe names in `is_supported()` can be preempted; temporary-file cleanup needs a contract that never deletes another process's files.
- An O_TMPFILE variant presented as closing the race: existence-check → publish/swap is not a CAS between the observed and the published state; two concurrent writers can still end last-writer-wins; `/proc/self/fd` + `linkat` needs real kernel/filesystem testing before being claimed as a guarantee.

**Impact on create_bottle():** stays BLOCKED. Safe atomic registry writes do not, by themselves, prove the directory-creation and prefix-registration path safe; re-enabling requires the independent review of the create protocol described above, not a registry mechanism swap.

### 1.2 Scope of the partial closure (contract, 2026-11-13)

This subsection is the binding contract for what the current code closes
and what it deliberately does not. It is written before the O_TMPFILE
commit lands on this branch.

**What is closed (by the code on this branch + its additions):**

- Target identity is verified before publication: regular file, owner
  is the current user, mode exactly 0600, hard-link count 1, opened
  no-follow relative to the managed-root descriptor.
- Identity is revalidated with ``fstat`` after every open, so a symlink
  or entry swap after open is refused.
- The temporary directory entry is matched against the held descriptor
  immediately before replace; a swap of the temp name is rejected.
- Partial writes are handled by ``_write_all`` — a short write can no
  longer truncate the payload silently.
- Temporary-file hygiene (new, 2026-11-13): the payload is written to an
  UNNAMED ``O_TMPFILE`` inode where the platform/filesystem supports it;
  the name exists only between the linkat attachment and the replace.
  A missing ``O_TMPFILE`` flag raises ``NotSupportedError`` (fail-closed,
  no named fallback). Where the filesystem refuses ``O_TMPFILE`` itself
  (proven: overlayfs → ENOTSUP), the previously reviewed hardened
  named-temporary protocol is used unchanged. This is hygiene ONLY.
- Cooperating writers serialize through the advisory lock; store batches
  are validated atomically before any transaction opens.

**What stays open (not closable by portable in-process POSIX code):**

- A hostile process under the same UID can still mutate directory
  entries in the final check-to-rename window. This is a DAC boundary;
  no in-process protocol closes it.
- There is no portable compare-and-swap on a directory entry.
  ``renameat2(RENAME_EXCHANGE)`` followed by verification was evaluated
  and REJECTED (§1.1): verify-after-exchange is not an atomic
  precondition, and the rollback itself is race-exposed.
- ``O_TMPFILE`` is a hygiene improvement only — it is NOT a same-UID
  closure and makes no CAS claim.
- Full closure requires an isolated broker with an independent system
  identity and a narrow authenticated IPC contract (§1.1 L3, v2 scope,
  outside v1.x).

**Impact on ``create_bottle()``:** remains BLOCKED for a reason that is
independent of the registry write protocol: ``mkdirat()`` does not
return a descriptor for the created inode, and prefix-path creation
races need their own contract review (see §3).

## 2. Evidence provenance boundary

The SQLite vocabulary constraint only restricts status strings; it cannot prove that a claim is authentic. The bridge already rejects PROVEN, but the low-level store previously accepted it. The public KnowledgeStore write boundary now also rejects new PROVEN values. Existing stored rows are not rewritten, and historical reads/exports are not reclassified by this change.

This is a containment control, **not a provenance verifier**. No caller-supplied status, digest, or repository-local metadata is sufficient to authenticate evidence. Recommendation authorization remains disabled until the trust-anchor decision, evidence envelope, local raw-evidence retention, deterministic verifier, freshness/revocation handling, and adversarial/mutation tests described in docs/decisions/2026-11-12-evidence-provenance-contract-proposal.md are approved and implemented.

**Batch atomicity (verified 2026-11-13, test-documented):** `add_records_atomic` validates the complete batch via `_prepare_record` before any write transaction opens, so a PROVEN record anywhere in a batch rejects the whole batch with zero rows inserted. `test_atomic_rejection_leaves_no_partial_insert` pins this property.

**Historical round-trip policy (decision 2026-11-13, unified plan §2.2):**

| Scenario | Behavior |
|---|---|
| Export of a historical PROVEN row | Exported exactly as stored; no downgrade on export, no data loss. |
| Import of a PROVEN record (default import path) | Downgraded to UNPROVEN; the original claim is preserved in the returned import report (`import_original_claim="PROVEN"`, `import_source="<source>:<index>"`, `import_warning="downgraded_from_PROVEN"`, plus `index`, `inserted_id`, `original_evidence_status`). The caller must persist that report as the import log so no provenance semantics are silently lost. |
| Import of a PROVEN record with ``strict=True`` | Rejected with ``PortabilityError``, all-or-nothing; nothing is inserted. |
| Recommendation relying on any PROVEN row | Remains disabled until a real verifier with a documented trust anchor exists. |

Placement note (honest scoping): the original claim is preserved in the
import report, not embedded as columns in the stored row. The canonical
v4 store schema is frozen; embedding `import_original_claim`/
`import_source`/`import_warning` as row columns would require a v5
schema migration, which is deliberately out of scope for this round and
would touch export, queries, and every migration path.

## 3. Create Bottle

create_bottle() must continue to return BLOCKED and perform zero filesystem writes. Tests must assert no directory, lock, registry file, audit log, or Wine process is created. Do not re-enable the historical mkdir/open implementation. The old design is retained only as historical review material.

## 4. CAT-tool compatibility evidence

A successful Python test suite, wheel build, recipe JSON validation, Wine version probe, or Vulkan probe does not establish Trados/memoQ compatibility.

For each application/version, record at minimum:
- application name and exact version/build;
- installer provenance and license entitlement (never commit installer or license files);
- Manjaro release, kernel, architecture, desktop session, GPU/driver, Wine build and relevant component versions;
- fresh disposable prefix identity and exact recipe revision;
- installation, first launch, sign-in/licensing flow where permitted, create/open/save/export of a synthetic non-sensitive project, restart, and clean shutdown;
- observed failures and sanitized logs, with private paths, usernames, tokens, customer data, and translation content removed.

A compatibility claim is version-specific. Until this matrix is executed and reviewed, keep both recipes UNPROVEN and last_tested: null.

## 5. Manjaro host validation

Ubuntu GitHub Actions are useful for portable Python tests but are not a substitute for the target distribution. The release gate requires a clean Manjaro VM or host with a disposable account and no production Wine prefix.

Minimum checks:
1. Install the built wheel/package in a clean environment using the documented offline/local-first path.
2. Run wine-ai --help and read-only diagnostics with command execution disabled and enabled only for explicitly allow-listed probes.
3. Verify Wine discovery, Vulkan probe behavior when the relevant tools/drivers exist, and graceful UNPROVEN output when they do not.
4. Run the complete stdlib unit suite and wheel/package smoke tests on that host.
5. Verify no mutation of ~/.wine, no unexpected filesystem writes, and no network/cloud fallback.
6. Attach a sanitized evidence report to the PR. Do not attach prefix archives, proprietary installers, license files, translation memories, customer documents, or raw logs.

Until these checks are recorded, mark target-Manjaro runtime validation NOT RUN. Do not infer success from the developer's distro or from an Ubuntu CI runner.

## 6. Portability of the identity gates (review round 2026-11-13)

A follow-up read-only review of this branch (2026-11-13) found two
environment-dependent defects that GitHub CI (ext4, umask 022) cannot
see; both are fixed by the portability commit on this branch and both
keep the fail-closed direction.

**N-1 — discovery denied service on layered filesystems (fixed).**
The enumeration step compared readdir's `d_ino` with statx `st_ino` and
blocked every candidate on mismatch. On overlayfs the two values differ
structurally for every entry (proven 8/8 in a sandboxed repro), so all
candidates were denied regardless of attacker activity — 17
deterministic test failures in that environment. Identity binding now
relies solely on the no-follow stat snapshot taken during enumeration
compared with `fstat` of the descriptor opened `dir_fd`-relative to the
scanned root (same stat family); the deterministic
swap-after-enumeration test still guards the race, and a new regression
test simulates overlayfs-style divergence and asserts discovery
succeeds.

**N-2 — registry refused standard user-private-group desktops
(fixed, policy documented).**
The managed-path gate rejected any group-writable component, including
user-owned `~/.local` and `~/.local/share`. On Debian/Ubuntu
user-private-group desktops (umask 002) those are 0775 owned by the
user's own primary group, so the registry refused to open entirely for
a large class of real users — 21 test errors in that environment. The
gate now: rejects world-writable components always; accepts a
group-writable XDG parent only when the owning group is the user's
primary group (the group is the user alone); keeps application-owned
components at exactly 0700. A group-writable component owned by any
other group is rejected. Residual, honestly stated: on a system where
an attacker is a member of the user's primary group, that group
writability is a real risk; such setups are not the standard
user-private-group pattern and remain blocked. Test fixtures now pin
every managed component so the suite is hermetic under umask 002, 022,
and 077 (proven: 385 tests OK under all three in the sandboxed repro).

**Disposition:** both fixes preserve the fail-closed direction; neither
weakens the same-UID race boundary described in section 1, which remains
OPEN.

## Merge/release policy

- Keep Create Bottle blocked.
- Keep compatibility claims UNPROVEN.
- Do not claim the Registry same-UID race is fixed.
- Reject new caller-supplied PROVEN writes at the store API.
- Require CI on the exact final PR head.
- Require a separate independent security review before any change that enables filesystem creation or recommendation authorization.
