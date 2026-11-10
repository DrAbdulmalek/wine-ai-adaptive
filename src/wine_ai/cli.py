"""Read-only diagnostic status CLI for wine-ai-adaptive.

This tool only observes: it never modifies Wine, never touches a
prefix, never stores raw logs, and never calls a remote AI provider.

Usage:
    wine-ai status          # human-readable summary
    wine-ai status --json   # structured JSON payload
"""

from __future__ import annotations

import argparse
import json
import signal
import sys

try:  # POSIX: die silently when the consumer closes the pipe (e.g. head)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):  # pragma: no cover - non-POSIX
    pass

from wine_ai.environment.inventory import collect_environment
from wine_ai.gpu_compatibility.probe import probe_vulkan


def _evidence_to_dicts(evidence_items):
    return [item.to_dict() for item in evidence_items]


def status_command(as_json: bool) -> int:
    environment, environment_evidence = collect_environment()
    gpu, gpu_evidence = probe_vulkan()

    evidence = _evidence_to_dicts(
        list(environment_evidence) + list(gpu_evidence)
    )

    if as_json:
        payload = {
            "environment": environment,
            "gpu": gpu,
            "evidence": evidence,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("===== WINE AI ADAPTIVE STATUS =====")
    print(f"Python: {environment.get('python', 'unknown')}")
    print(f"Wine: {environment.get('wine_version', 'Not detected')}")
    print(f"Vulkan available: {gpu.get('available', False)}")

    devices = gpu.get("device_names") or []
    if devices:
        print("GPU devices:")
        for device in devices:
            print(f"  - {device}")

    print("Evidence:")
    for item in evidence:
        print(f"  - {item['key']}: {item['status']} - {item['summary']}")

    print("===== END STATUS =====")
    return 0


def _add_json_flag(parser, *, suppressed=False):
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS if suppressed else False,
        help=None if suppressed
        else "Emit structured JSON instead of human-readable text.",
    )


def main(argv: list[str] | None = None) -> int:
    # --json is accepted both before and after the subcommand
    # ("wine-ai --json status" and "wine-ai status --json").
    parser = argparse.ArgumentParser(
        prog="wine-ai",
        description="Local-first Wine diagnostic status tool.",
    )
    _add_json_flag(parser)

    subparsers = parser.add_subparsers(dest="command")
    status_parser = subparsers.add_parser(
        "status",
        help="Read-only environment and GPU status.",
    )
    # SUPPRESS default: only override the top-level flag when explicitly
    # repeated after the subcommand.
    _add_json_flag(status_parser, suppressed=True)

    args = parser.parse_args(argv)

    command = args.command or "status"

    if command == "status":
        return status_command(as_json=bool(args.json))

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
