# Wine prefix isolation and safety

## A prefix is application state

A Wine prefix contains registry data, installed components, configuration, and application state. Treat it like a separate test machine. Trados and memoQ must never share a prefix for compatibility experiments.

## Required isolation

- Keep production and test prefixes separate.
- Use a dedicated prefix per application and, when needed, per tested application release.
- Never use the default `~/.wine` prefix for experiments; the project treats it as protected.
- Never point experiments at a prefix containing licensed production software or customer work.
- Record the prefix's purpose and the exact software versions in a local test record.

## Current project capabilities

`wine-ai prefixes` discovers prefixes and reports structured information; it does not create, initialize, clone, back up, restore, or delete them.

Bottle creation through the current Bottle Manager is **disabled** and returns `BLOCKED` without filesystem writes. Do not rely on it to create even an empty directory. The feature remains gated pending a stronger, independently reviewed security design. Initialization and application execution are also outside the current safe management interface.

Do not infer that a directory named `trados-test` or `memoq-test` is a valid prefix. Do not run `wineboot -u` merely to make the directory look initialized.

## Before any future prefix creation or initialization

1. Confirm the exact target path and that it is not an existing or symlinked directory.
2. Confirm the path is not `~/.wine`, a production prefix, or a path containing user data.
3. Confirm the application-specific plan, Wine version, architecture, and required components.
4. Confirm backup/recovery options and available disk space.
5. Obtain explicit user approval for the exact write operation.
6. Keep the operation auditable and verify the resulting prefix independently.

This repository's read-only CLI does not perform those write operations.
