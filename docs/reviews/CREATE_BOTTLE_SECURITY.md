# Create Bottle — Security Contract (P2)

**Status:** implementation proposal in Draft PR; independent review required.  
**Scope:** create one empty, uninitialized directory below the managed prefixes root.  
**Out of scope:** Wine initialization, Clone, Backup, Restore, Delete, migration, and modification of existing prefixes.

## Maintainer decisions recorded 2026-11-11

The Create Bottle threat model T-CB-01…T-CB-10, detailed design, and security test plan were accepted in the project conversation. The audit-log exception was also accepted: a rejected request may create or append to the separate audit facility, but must not create a Bottle or any object below `prefixes/`. This record does not authorize merging this Draft PR.

## Security design

1. Names are restricted to 1–64 ASCII alphanumeric, hyphen, or underscore characters, with an alphanumeric first character.
2. The only destination is `~/.local/share/wine-ai-adaptive/prefixes/<name>`; no user-supplied path is accepted.
3. Home and managed path components are opened incrementally with directory descriptors, `O_DIRECTORY`, and `O_NOFOLLOW`; opened components are checked against their pre-open device/inode identity.
4. Application-owned directories must be owned by the effective user and have mode `0700`. Existing directories with weaker or unexpected permissions are rejected, not silently repaired.
5. The managed prefixes root is rejected if its identity aliases protected `~/.wine`, including a symlinked `~/.wine` that resolves to that root.
6. The target is created relative to the managed-root descriptor. Existing files, directories, and symlinks are never replaced.
7. The new directory is opened without following links, checked with `fstat`, set to `0700` through its descriptor, and verified again. Cleanup is limited to the still-empty object whose opened identity matches the created identity.
8. Audit records are appended to `~/.local/share/wine-ai-adaptive/logs/create-bottle.jsonl`, outside the prefixes tree. The log is a user-owned regular file with mode `0600`, opened with `O_NOFOLLOW`. Records contain a relative path only; no absolute home path is persisted.
9. Success is reported as `EMPTY_UNINITIALIZED`. No Wine, winetricks, shell, subprocess, or external command is invoked.

## Audit-log exception

For invalid or rejected requests, no Bottle and no entry under `prefixes/` may be created. The only permitted side effect is creating/appending the separate audit log. If the audit facility cannot be opened and validated, the operation is refused before Bottle creation.

## Residual limitations

- A privileged concurrent actor able to manipulate mounts or the filesystem namespace can still race user-space operations. Descriptor-relative operations reduce exposure but do not prove race impossibility.
- If a directory is created but cannot subsequently be opened and its identity cannot be established, the implementation must not delete an object it cannot prove it owns. Such an adversarial failure may leave an empty directory requiring manual inspection; this is safer than deleting a replacement object.
- Audit durability is limited to host filesystem and `fsync` guarantees.

## Test mapping

`tests/unit/test_bottle_create_security.py` uses stdlib `unittest` and temporary directories only. It covers validation, confinement, symlinks, protected-root aliasing, pre-existing targets, ownership/mode/type, cleanup, audit success/failure/privacy, no external execution, and repeated descriptor lifecycle.

## Review gates

- Review the exact PR head and all tests.
- Require CI success on the exact head. The configured push workflow runs Python 3.11/3.12/3.13 for `security/**`; the current `pull_request` workflow filter targets `main`, so a PR based on the P1-D branch is not represented as a `pull_request` CI event.
- Obtain an independent security review before merge consideration.
- Keep the PR Draft until the maintainer makes a separate merge decision.
