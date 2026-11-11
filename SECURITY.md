# Security Policy

This project is designed to avoid storing raw diagnostic logs, private
paths, Wine prefix contents, registry contents, DLL contents, prompts,
or credentials.

## Safety boundaries

- No automatic repair of Wine or the system.
- No Wine prefix modification, no registry or DLL changes.
- No root privileges required or requested.
- No remote AI provider by default (offline-first, local Ollama
  inventory only, never sends prompts).
- No raw log persistence: only structured, allow-listed fields reach
  the database, with SQLite `CHECK` constraints as the last line of
  defense.
- Fail closed for security-sensitive paths (symlinks, non-regular
  files, foreign ownership, incompatible schemas are rejected).

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the full
threat-to-mitigation mapping and the honestly documented residual
risks, and [docs/AGENT_POLICY.md](docs/AGENT_POLICY.md) for the rules
any automated agent must follow when writing to this repository.

## Reporting a vulnerability

Please use GitHub private security advisories if available, or contact
the maintainer privately.

Do not open public issues containing:

- raw logs
- tokens
- private file paths
- Wine prefix contents
- registry dumps
- executable contents
- personal medical or system identifiers
