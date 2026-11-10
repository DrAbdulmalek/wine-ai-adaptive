import subprocess
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None = None


def run_read_only(
    argv: Sequence[str],
    timeout: float = 8.0
) -> CommandResult:

    args = tuple(str(x) for x in argv)

    if not args or any("\x00" in x for x in args):
        return CommandResult(
            args, None, "", "", "invalid arguments"
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
