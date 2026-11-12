# Getting Started: Trados Studio and memoQ on Linux

> **Compatibility warning:** This guide does not promise that either application works under Wine. Both bundled recipes are currently **UNPROVEN**, with no recorded field-test result. The project provides local diagnostics; it is not an installer or an automatic repair tool.

## What this project does

`wine-ai-adaptive` provides local Python tools to inspect the environment, discover Wine prefixes, and analyze Wine logs. Its current CLI commands are read-only: they do not create or initialize prefixes, install Trados or memoQ, run winetricks suggestions, or repair the system.

## 1. Safe preflight

On Manjaro/Arch, collect basic information without changing the system:

```bash
uname -a
cat /etc/os-release
python3 --version
command -v wine || true
command -v winetricks || true
command -v vulkaninfo || true
free -h
df -h "$HOME"
```

If you require a strictly non-executing inspection, do not run `wine --version`: that invokes the Wine executable.

## 2. Install the diagnostic tool

Python 3.11 or newer is required. No root privileges are needed:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/DrAbdulmalek/wine-ai-adaptive.git
cd wine-ai-adaptive
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 3. Inspect the host and prefixes

```bash
wine-ai status
wine-ai status --json
wine-ai prefixes
wine-ai prefixes --json
```

Prefix discovery does not create or modify prefixes. The default `~/.wine` prefix is treated as protected and should not be used for CAT application experiments.

## 4. Obtain installers

Download installers only through the vendor's official website/account or authorized download channel:
- Trados Studio: RWS official website/account.
- memoQ: memoQ official website/account.

Check the exact application release, vendor requirements, and licensing terms. Do not use modified installers or unknown file-sharing links. Availability of an installer does not imply Wine compatibility.

## 5. Keep experiments isolated

Use a dedicated test environment for each application. Never mix Trados and memoQ files, and do not experiment in `~/.wine` or a production prefix. Do not create or initialize a prefix until the exact release requirements and recovery plan have been reviewed.

Bottle creation through the current Bottle Manager is **disabled** and returns `BLOCKED` without filesystem writes. Do not rely on it as a way to create a prefix; the feature remains gated pending a stronger, independently reviewed security design.

## 6. Diagnose a failure

Keep the Wine log local and run the matching profile:

```bash
wine-ai diagnose --log /path/to/wine.log --profile trados
wine-ai diagnose --log /path/to/wine.log --profile memoq
```

The output contains heuristic triage hints, not proof of root cause. Any winetricks commands shown are suggestions only; the project never executes them.

## Privacy

- Never upload translation documents, translation memories, termbases, project files, credentials, or customer data.
- Do not share license keys, activation tokens, or account information.
- Review logs before sharing; they may expose usernames, paths, filenames, server names, or other sensitive metadata.
- Do not send confidential or medical/professional data to cloud AI services.
- Prefer local analysis and share only carefully redacted excerpts when necessary.

## Evidence status

Both bundled recipes remain `UNPROVEN` with `last_tested: null`. Do not mark either application as working until the exact application/Wine/host versions, dependencies, licensing, connectivity, and functional tests have been recorded. See [TEST_MATRIX.md](TEST_MATRIX.md) and [EVIDENCE_POLICY.md](EVIDENCE_POLICY.md).

Further reading: [Installation](INSTALLATION.md), [Wine prefixes](WINE_PREFIXES.md), [Dependencies](DEPENDENCIES.md), and [Troubleshooting](TROUBLESHOOTING.md).
