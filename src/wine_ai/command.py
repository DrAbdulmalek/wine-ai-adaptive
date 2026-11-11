"""Read-only subprocess execution for local diagnostics.

``run_read_only`` is NOT a generic sandbox. It enforces a strict
allow-list: only known read-only diagnostic programs with known
read-only arguments may run, always with ``shell=False``, a timeout and
capped output capture. Any other program or argument fails closed with
an error result instead of executing.
"""

import subprocess
from dataclasses import dataclass
from typing import Sequence


# Allow-list of diagnostic programs (matched by basename of argv[0],
# case-insensitively) and the exact arguments they may receive.
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


def _is_allowlisted(args: tuple[str, ...]) -> bool:
    program = args[0].rsplit("/", 1)[-1].lower()
    allowed_args = READ_ONLY_COMMANDS.get(program)
    if allowed_args is None:
        return False
    return all(arg in allowed_args for arg in args[1:])


def run_read_only(
    argv: Sequence[str],
    timeout: float = 8.0
) -> CommandResult:

    args = tuple(str(x) for x in argv)

    if not args or any("\x00" in x for x in args):
        return CommandResult(
            args, None, "", "", "invalid arguments"
        )

    if not _is_allowlisted(args):
        return CommandResult(
            args,
            None,
            "",
            "",
            "command not in read-only allow-list",
        )

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )

        return CommandResult(
            args,
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
