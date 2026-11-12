"""Read-only diagnostic CLI for wine-ai-adaptive.

This tool only observes: it never modifies Wine, never touches a
prefix, never stores raw logs, never writes a database, and never
calls a remote AI provider. The ``diagnose`` subcommand reads a local
log file and prints structured findings only — nothing is persisted.

Usage:
    wine-ai status                    # human-readable summary
    wine-ai status --json             # structured JSON payload
    wine-ai diagnose --log FILE       # triage a local Wine log (read-only)
    wine-ai diagnose --log FILE --json
    wine-ai prefixes                  # discover Wine prefixes (read-only)
    wine-ai prefixes --json
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

from pathlib import Path

from wine_ai.bottle_manager.discovery import discover_prefixes
from wine_ai.diagnostics.cat_tools import (
    CAT_PROFILE_NAMES,
    get_profile,
    refine_findings,
    signature_hints,
)
from wine_ai.diagnostics.codes import extract_error_codes
from wine_ai.diagnostics.log_parser import inspect_log_with_content
from wine_ai.environment.inventory import collect_environment
from wine_ai.evidence import Evidence, EvidenceStatus
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


def diagnose_command(log_path: str, as_json: bool, profile: str | None = None) -> int:
    """Triage a local Wine/Proton log: parse, classify, print.

    Strictly read-only: the log is never stored, no database is
    created, and the output contains only structured codes, heuristic
    categories and confidence levels — never raw log lines.

    With ``profile`` set to a CAT tool name (``trados`` or ``memoq``)
    the findings gain CAT-specific hints and *suggestion-only*
    winetricks commands. Suggestions are printed for the user to run
    manually; this tool never executes them (docs/AGENT_POLICY.md,
    no auto-repair).
    """
    summary, evidence, text = inspect_log_with_content(log_path)

    findings = []
    profile_hints = []
    profile_analyzed = False
    if "read_error" not in summary and text is not None:
        try:
            findings = extract_error_codes(text)
            if profile is not None:
                findings = refine_findings(findings, profile)
                profile_hints = signature_hints(text)
                profile_analyzed = True
        except (OSError, ValueError) as exc:
            evidence = evidence + [
                Evidence("diagnose_read", EvidenceStatus.BLOCKED,
                         "Log analysis failed after the single safe read",
                         str(log_path), {"error": str(exc)})
            ]

    if as_json:
        payload = {
            "log": str(Path(log_path).expanduser()),
            "findings": findings,
            "log_summary": summary,
            "evidence": _evidence_to_dicts(evidence),
        }
        if profile_analyzed:
            payload["profile"] = profile
            payload["profile_hints"] = profile_hints
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("===== WINE AI ADAPTIVE DIAGNOSE =====")
    print(f"Log: {Path(log_path).expanduser()}")

    if findings:
        print("Structured error codes:")
        for item in findings:
            category = item["category"] or "unmapped"
            line = (
                f"  - {item['kind']:<8} {item['code']:<16} "
                f"category={category:<16} confidence={item['confidence']}"
            )
            if profile is not None and item.get("cat_hint"):
                line += f" cat_hint={item['cat_hint']}"
            print(line)
    else:
        print("No structured error codes found.")

    if profile is not None:
        meta = get_profile(profile)
        print(f"CAT profile: {meta['display_name']} — {meta['status']}")
        if profile_hints:
            print("CAT signature hints (heuristic, triage only):")
            for hint in profile_hints:
                print(
                    f"  - {hint['hint']:<24} matches={hint['matches']} "
                    f"confidence={hint['confidence']}"
                )
                print(f"      suggestion (NOT executed): {hint['winetricks_suggestion']}")
        else:
            print("CAT signature hints: none matched.")
        print("WineHQ references:")
        for bug in meta["winehq_bugs"]:
            print(f"  - {bug}")

    pattern_hits = summary.get("matches", {})
    if pattern_hits:
        print("Pattern hits:")
        for name, count in pattern_hits.items():
            print(f"  - {name}: {count}")

    print("Evidence:")
    for item in _evidence_to_dicts(evidence):
        print(f"  - {item['key']}: {item['status']} - {item['summary']}")

    print("NOTE: findings are heuristic triage hints, not root-cause "
          "proof. Nothing was stored or modified.")
    print("===== END DIAGNOSE =====")
    return 0


def prefixes_command(as_json: bool) -> int:
    """Discover local Wine prefixes — strictly read-only.

    Implements the P0 read-only tier of the CrossOver clean-room
    spec (docs/CROSSOVER_CLEAN_ROOM_SPEC.md, section 6.4): discover,
    inspect, validate — never create, clone, back up, restore or
    delete. Nothing is stored; ``~/.wine`` is reported as protected.
    """
    prefixes, evidence = discover_prefixes()

    if as_json:
        payload = {
            "prefixes": prefixes,
            "evidence": _evidence_to_dicts(evidence),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("===== WINE AI ADAPTIVE PREFIXES =====")
    print(f"Discovered: {len(prefixes)}")
    for prefix in prefixes:
        flags = []
        if prefix["protected"]:
            flags.append("protected")
        if prefix["architecture"]:
            flags.append(prefix["architecture"])
        if prefix["arch_conflict"]:
            flags.append("conflicting-signals")
        suffix = "".join(f" ({flag})" for flag in flags)
        print(f"  - {prefix['name']} [{prefix['origin']}]{suffix}")
        print(f"      {prefix['path']}")
    if not prefixes:
        print("  (none)")

    print("Evidence:")
    for item in _evidence_to_dicts(evidence):
        print(f"  - {item['key']}: {item['status']} - {item['summary']}")

    print("NOTE: read-only discovery — nothing was modified or stored.")
    print("Write operations (create/clone/backup/restore/delete) are "
          "not implemented by design.")
    print("===== END PREFIXES =====")
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

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Read-only triage of a local Wine/Proton log "
             "(prints findings, stores nothing).",
    )
    diagnose_parser.add_argument(
        "--log",
        required=True,
        help="Path to a local Wine/Proton log file (max 2 MiB).",
    )
    diagnose_parser.add_argument(
        "--profile",
        choices=list(CAT_PROFILE_NAMES),
        default=None,
        help="CAT tool profile for refined hints and suggestion-only "
             "winetricks commands (nothing is executed).",
    )
    _add_json_flag(diagnose_parser, suppressed=True)

    prefixes_parser = subparsers.add_parser(
        "prefixes",
        help="Read-only discovery of local Wine prefixes "
             "(no modification, no storage).",
    )
    _add_json_flag(prefixes_parser, suppressed=True)

    args = parser.parse_args(argv)

    command = args.command or "status"

    if command == "status":
        return status_command(as_json=bool(args.json))

    if command == "diagnose":
        return diagnose_command(
            log_path=args.log,
            as_json=bool(args.json),
            profile=getattr(args, "profile", None),
        )

    if command == "prefixes":
        return prefixes_command(as_json=bool(args.json))

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
