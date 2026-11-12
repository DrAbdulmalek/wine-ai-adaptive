# Project status

**Status date:** 2026-11-12  
**Status source:** this file is the canonical snapshot for the unreleased security-hardening work.  
**Current release:** `v1.0.0` (published 2026-11-11; do not rewrite or retag).

## Executive status

The project is a local-first Wine diagnostic and evidence-storage library. It is **not currently a Wine-prefix lifecycle manager** and it does not establish that Trados Studio or memoQ run successfully under Wine.

The open security-hardening change in PR #17 intentionally disables Bottle creation. This is a fail-closed restriction while the same-UID directory-entry race at the `mkdirat()`/`openat()` boundary remains unresolved. Read-only discovery, inspection, registry reads, and orphan reporting remain the intended supported BottleManager surface.

## Release and compatibility status

| Item | Status | Evidence / qualification |
|---|---|---|
| Published package version | `1.0.0` | GitHub Release `v1.0.0` was published on 2026-11-11. Its release notes describe the pre-hardening integrated scope. |
| Rewriting or downgrading `v1.0.0` | **Not permitted** | The published release/tag is historical. Do not reuse, move, or silently redefine it. |
| Current unreleased hardening branch | **Draft / review required** | PR #17 contains security changes and the Create Bottle feature gate. |
| Create Bottle in PR #17 | **BLOCKED / disabled** | Both public creation entry points return `BLOCKED` without filesystem writes. Do not re-enable until a reviewed OS-level identity-binding design and tests exist. |
| Registry writes | **Restricted / same-UID race remains** | Managed XDG path components must be owned by the current user and not group/other-writable; the app and prefixes directories must be `0700`. Parent operations are pinned with `dir_fd`; temp/target identity checks narrow the race. `flock` coordinates cooperating writers only. The final check-to-rename interval is not atomic against a hostile same-UID process. |
| Registry reads and orphan reporting | **Read-only** | Reads pin one registry inode; reads do not create the writer lock. Orphan reporting does not delete or repair anything. |
| Trados Studio / memoQ runtime compatibility | **UNPROVEN** | Recipe/profile presence and successful wheel construction are not runtime compatibility evidence. No claim of successful installation, activation, licensing, or stable execution is made. |
| Manjaro operational readiness | **NOT VERIFIED** | CI and wheel builds do not substitute for an installation and controlled runtime test on the target Manjaro environment. |

## Security decisions

### Registry atomicity

Before registry access, managed XDG path components are checked for current-user ownership and group/other non-writability; the application and prefixes directories must be exactly `0700`. The implementation uses an already-open registry-root directory descriptor for temporary-file creation, entry inspection, and `os.replace(..., src_dir_fd=..., dst_dir_fd=...)`. It compares `lstat`-style entry metadata with `fstat` of the open temporary descriptor, and rechecks the target identity after the temporary file is synced.

These checks detect important substitutions and narrow the race window. They **do not provide an atomic compare-and-swap by inode**: a same-UID actor able to mutate the directory can still race between the final identity check and rename. The project must not describe this as complete TOCTOU elimination.

Linux-specific `O_TMPFILE` / `linkat` / `renameat2(RENAME_EXCHANGE)` remains a design option, not an implemented guarantee. Any future implementation must define crash consistency, publication ordering, unsupported-filesystem behavior, and an explicitly weaker or fail-closed fallback before use.

Private directory modes such as `0700` reduce exposure to other UIDs; they do not isolate against a malicious process running as the same UID.

### Evidence provenance

The existing knowledge store validates record shape and allowed vocabulary. A caller-supplied `evidence_status` value is not proof of provenance. No trusted evidence verifier is claimed by this status file. A recommendation engine must remain gated until a verifiable provenance contract and reproducible verifier exist; a stored `PROVEN` label alone must never authorize a recommendation.

### Secrets

Secret rotation is an owner-operated incident-response action. Repository contents or CI status cannot establish that credentials have been revoked and rotated. Do not place secret values in issues, commits, logs, or chat.

## Release policy

- Keep the published `v1.0.0` immutable as historical release metadata.
- Describe the Create Bottle disablement as an unreleased security restriction in the current PR and changelog.
- Decide the next package version only when preparing a new release; use a new version/tag and release notes that accurately describe the restricted feature set.
- Do not call the package operationally ready for CAT applications until reproducible, documented tests have been run on the target Manjaro environment.

## Work queue

1. **P0 — Registry and Create Bottle security:** preserve the fail-closed gate; review residual namespace races and document the threat boundary.
2. **P0 — Release-scope correction:** make the difference between published `v1.0.0` and the unreleased hardening branch explicit; do not mutate the published release.
3. **P1 — Evidence-gated recommendations:** define provenance fields, trust anchors, raw-evidence retention/availability, deterministic re-derivation, and negative/mutation tests before implementing a verifier.
4. **P2 — Secret scanning:** add scanning and document owner-controlled rotation status without recording secret values.
5. **P3 — Documentation/release readiness:** keep this file as the canonical status snapshot and reconcile README/CHANGELOG/release notes against actual behavior.
6. **P4 — Target-platform validation:** install the built wheel in an isolated environment and run controlled Manjaro tests; do not modify the user's Wine prefixes as part of CI or repository work.

## Change control

All security changes are developed on a topic branch and reviewed through a Draft PR. No direct commits to `main`, no force-push, no history rewriting, and no automatic merge. A CI pass proves only the checks that actually ran; it does not prove runtime compatibility or eliminate documented residual races.
