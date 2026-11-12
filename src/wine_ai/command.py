"""Read-only subprocess execution for local diagnostics.

``run_read_only`` is NOT a generic sandbox. It enforces a strict
allow-list: only known read-only diagnostic programs with known
read-only arguments may run, always with ``shell=False``, a timeout and
capped output capture. Any other program or argument fails closed with
an error result instead of executing.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Sequence


# Allow-list of diagnostic programs. Callers must use the bare command name;
# the resolved executable and every parent directory are checked before use.
READ_ONLY_COMMANDS: dict[str, frozenset[str]] = {
    "wine": frozenset({"--version"}),
    "wine64": frozenset({"--version"}),
    "vulkaninfo": frozenset({"--summary"}),
}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None = None


def _resolve_allowlisted_program(args: tuple[str, ...]) -> tuple[str, ...] | None:
    if len(args) != 2:
        return None
    program = args[0].lower()
    allowed_args = READ_ONLY_COMMANDS.get(program)
    if allowed_args is None or args[1] not in allowed_args:
        return None
    if "/" in args[0] or "\\" in args[0]:
        return None

    located = shutil.which(program)
    if located is None:
        return None
    executable = Path(os.path.realpath(located))
    try:
        info = executable.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK):
            return None
        trusted_owners = {0, os.geteuid() if hasattr(os, "geteuid") else info.st_uid}
        if info.st_uid not in trusted_owners or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return None
        for parent in executable.parents:
            parent_info = parent.stat()
            if not stat.S_ISDIR(parent_info.st_mode):
                return None
            if parent_info.st_uid not in trusted_owners:
                return None
            if parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return None
    except OSError:
        return None
    return (str(executable), args[1])


def run_read_only(
    argv: Sequence[str],
    timeout: float = 8.0
) -> CommandResult:

    args = tuple(str(x) for x in argv)

    if not args or any("\x00" in x for x in args):
        return CommandResult(
            args, None, "", "", "invalid arguments"
        )

    resolved_args = _resolve_allowlisted_program(args)
    if resolved_args is None:
        return CommandResult(
            args,
            None,
            "",
            "",
            "command not in read-only allow-list",
        )

    try:
        result = subprocess.run(
            resolved_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )

        return CommandResult(
            resolved_args,
            result.returncode,
            result.stdout[:200_000],
            result.stderr[:20_000],
        )

    except (
        FileNotFoundError,
        PermissionError,
        subprocess.TimeoutExpired,
        OSError,
    ) as exc:

        return CommandResult(
            args,
            None,
            "",
            "",
            f"{type(exc).__name__}: {exc}",
        )
