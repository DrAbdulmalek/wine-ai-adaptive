# Troubleshooting workflow

## 1. Preserve the failure evidence

- Record the application release, installer source, Wine version, distribution/kernel, and prefix purpose.
- Keep the original log private and unchanged.
- Do not include translation content, customer identifiers, license data, or credentials in a shared report.

## 2. Run local diagnostics

For general Wine log triage:

```bash
wine-ai diagnose --log /path/to/wine.log
```

For CAT-specific hints:

```bash
wine-ai diagnose --log /path/to/wine.log --profile trados
wine-ai diagnose --log /path/to/wine.log --profile memoq
```

The parser extracts structured codes and heuristic patterns. It does not establish root cause, verify application compatibility, or persist the raw log. The CLI may print the supplied log path; redact paths before sharing terminal output.

## 3. Interpret results conservatively

- A missing-module or runtime hint is a lead to investigate, not proof that a dependency is absent.
- A suggested winetricks command is text only; the project does not run it.
- No matching signature does not prove that the application or Wine is healthy.
- A successful installer launch does not prove activation, stable startup, document handling, plugins, printing, updates, or server connectivity.

## 4. Change one variable at a time

If you independently decide to test a change, use a disposable isolated prefix, record the exact change, and capture before/after results. Do not alter a production prefix or run broad winetricks bundles as a guess. Obtain explicit approval before any write operation to a managed environment.

## 5. Escalation and alternatives

If the exact application release is not viable under Wine, use a vendor-supported configuration where available, a supported web edition, or a properly licensed virtual machine. Check current vendor documentation before relying on an alternative.

## 6. What to include in a redacted report

- Application name and exact version.
- Wine version and distribution version.
- GPU/driver information only if relevant.
- Prefix architecture and whether it is disposable.
- Diagnostic output after removing usernames, home paths, server names, and all confidential data.
- Exact reproduction steps and the result of each test.
