import platform
import shutil

from wine_ai.command import run_read_only
from wine_ai.evidence import Evidence, EvidenceStatus


TOOLS = (
    "wine",
    "wineserver",
    "vulkaninfo",
    "ollama",
    "git",
)


def collect_environment():

    data = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "executables": {
            name: shutil.which(name)
            for name in TOOLS
        },
    }

    evidence = [
        Evidence(
            "python_runtime",
            EvidenceStatus.PROVEN,
            f"Python {platform.python_version()}",
            "platform.python_version",
        )
    ]

    wine_path = data["executables"]["wine"]

    if wine_path:

        # run_read_only resolves the bare allow-listed command itself and
        # verifies the resolved executable before execution. Passing the
        # absolute path returned by shutil.which would correctly be rejected.
        result = run_read_only([
            "wine",
            "--version",
        ])

        if result.returncode == 0 and result.stdout.strip():

            data["wine_version"] = result.stdout.strip()

            evidence.append(
                Evidence(
                    "wine_version",
                    EvidenceStatus.PROVEN,
                    data["wine_version"],
                    "wine --version",
                )
            )

        else:

            evidence.append(
                Evidence(
                    "wine_version",
                    EvidenceStatus.BLOCKED,
                    "Wine version query failed",
                    "wine --version",
                    {
                        "error": result.error,
                        "stderr": result.stderr,
                    },
                )
            )

    else:

        evidence.append(
            Evidence(
                "wine_version",
                EvidenceStatus.UNPROVEN,
                "Wine executable not found",
                "shutil.which",
            )
        )

    return data, evidence
