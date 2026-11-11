# Installation and preflight

## Scope

This document covers installing the **wine-ai-adaptive diagnostic Python package**. It does not install Wine, winetricks, Trados Studio, or memoQ. It does not create a Wine prefix.

## Requirements

- Linux host (Manjaro/Arch is the primary example).
- Python 3.11 or newer.
- Git.
- User-owned writable project directory.
- No root privileges are required for the Python virtual environment workflow.

## Install from source

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/DrAbdulmalek/wine-ai-adaptive.git
cd wine-ai-adaptive
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

To leave the environment later: `deactivate`. To use the CLI again, return to the repository and activate `.venv`.

## Verify the CLI

```bash
wine-ai --help
wine-ai status
wine-ai prefixes
```

These commands inspect host state. Note that the status collector may query external local executables depending on available environment hints; consult the source and use a controlled test environment if execution of host programs is not acceptable.

## Optional read-only host inventory

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

Do not add `sudo` to the project commands. Do not run `wineboot`, `winetricks`, or an installer as part of this installation procedure.

## Uninstall the Python package

With the project virtual environment activated:

```bash
python -m pip uninstall wine-ai-adaptive
deactivate
```

Remove the project checkout only after confirming it contains no logs or other user data you want to retain. This procedure does not remove Wine or any Wine prefixes.
