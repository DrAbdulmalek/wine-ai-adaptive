# Create Bottle — Security Contract (P2)

**Current status (2026-11-12): DISABLED — NOT APPROVED FOR USE.** The public `create_bottle()` API now returns `BLOCKED` without filesystem side effects. The former implementation design below is historical evidence, not an enabled capability.  
**Scope:** create one empty, uninitialized directory below the managed prefixes root.  
**Out of scope:** Wine initialization, Clone, Backup, Restore, Delete, migration, and modification of existing prefixes.

## Maintainer decisions recorded 2026-11-11

The earlier design and audit-log exception were discussed and accepted as a proposal, but the independent post-merge review found an unresolved same-UID namespace race between `mkdirat` and `openat`. The current owner disposition is to disable the public write path. No previous proposal constitutes approval to enable it.

## Historical security design (not currently active)

1. Names are restricted to 1–64 ASCII alphanumeric, hyphen, or underscore characters, with an alphanumeric first character.
2. The only destination is `~/.local/share/wine-ai-adaptive/prefixes/<name>`; no user-supplied path is accepted.
3. Home and managed path components are opened incrementally with directory descriptors, `O_DIRECTORY`, and `O_NOFOLLOW`; opened components are checked against their pre-open device/inode identity.
4. Application-owned directories must be owned by the effective user and have mode `0700`. Existing directories with weaker or unexpected permissions are rejected, not silently repaired.
5. The managed prefixes root is rejected if its identity aliases protected `~/.wine`, including a symlinked `~/.wine` that resolves to that root.
6. The target is created relative to the managed-root descriptor. Existing files, directories, and symlinks are never replaced.
7. The new directory is opened without following links, checked with `fstat`, set to `0700` through its descriptor, and verified again. Cleanup is limited to the still-empty object whose opened identity matches the created identity.
8. Audit records are appended to `~/.local/share/wine-ai-adaptive/logs/create-bottle.jsonl`, outside the prefixes tree. The log is a singly-linked, user-owned regular file with mode `0600`, opened with `O_NOFOLLOW`; hard-linked audit targets are rejected. Records contain a relative path only; no absolute home path is persisted. Appends are serialized with an advisory file lock; partial writes are rolled back to the previous file length before the operation is reported as failed.
9. Success is reported as `EMPTY_UNINITIALIZED`. No Wine, winetricks, shell, subprocess, or external command is invoked.

## Historical audit-log exception (not currently active)

For invalid or rejected requests, no Bottle and no entry under `prefixes/` may be created. The only permitted side effect is creating/appending the separate audit log. If the audit facility cannot be opened and validated, the operation is refused before Bottle creation.

## Residual limitations

- A privileged concurrent actor able to manipulate mounts or the filesystem namespace can still race user-space operations. Descriptor-relative operations reduce exposure but do not prove race impossibility.
- If a directory is created but cannot subsequently be opened and its identity cannot be established, the implementation must not delete an object it cannot prove it owns. Such an adversarial failure may leave an empty directory requiring manual inspection; this is safer than deleting a replacement object.
- Audit durability is limited to host filesystem and `fsync` guarantees.

## Current test mapping

`tests/unit/test_bottle_create_security.py` now verifies that valid and invalid
requests both return `BLOCKED` and create no directory, audit log, lock, or
registry entry. The previous A–H tests describe the retired implementation;
they are not evidence that the feature is safe to re-enable.

The same-UID namespace swap test was previously marked `expectedFailure`.
That failure is now treated as a release blocker, not an accepted residual.
The feature remains disabled until a different OS security boundary is
implemented and independently verified.

## Review gates

- Keep the public write API disabled.
- A future re-enable proposal must introduce a stronger OS boundary (for example, a separately privileged broker with a narrow protocol and auditable ownership model); an in-process mkdir/open sequence is insufficient against a hostile same-UID process.
- Require deterministic adversarial tests and CI success on the exact PR head.
- Obtain an independent security review and a separate owner decision before re-enabling any write capability.
