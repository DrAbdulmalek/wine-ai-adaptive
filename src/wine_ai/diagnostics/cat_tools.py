"""CAT tools (Trados Studio, memoQ) diagnostic refinement layer.

Origin: external DeepSeek review session (2026-11-11, turn 6), integrated
here per the smart-merge method recorded in docs/reviews/EXTERNAL_REVIEWS.md.
The session researched Wine compatibility for Trados Studio and memoQ
(38 sources, including WineHQ bugs 48152, 56929, 9433 and 13679) and
recommended CAT-specific error refinement plus *suggestion-only*
winetricks hints.

Design constraints honored by this module:

- Fine-grained hints (e.g. ``dotnet_runtime_missing``) deliberately do
  NOT extend the ``error_category`` vocabulary of KnowledgeStore:
  schema v3 locks that column via SQL ``CHECK`` constraints and schema
  stability was a documented decision. Hints are triage-layer only.
- Suggestions are plain strings printed to the user. Nothing is ever
  executed: the "no auto-repair" policy (docs/AGENT_POLICY.md) and the
  command allow-list (wine_ai.command) are untouched.
- Standard library only, offline-first: every mapping is a local table.
- Raw log text never leaves this module; the output carries hint
  identifiers, aggregate match counts and static suggestion strings.
- All hints are heuristic indicators for triage, not root-cause proof.
"""

from __future__ import annotations

import re

__all__ = [
    "CAT_PROFILE_NAMES",
    "get_profile",
    "signature_hints",
    "refine_findings",
]

# ---------------------------------------------------------------------------
# Profile metadata (documented reality, not a compatibility promise).
# See docs/CAT_TOOLS_DIAGNOSTICS.md for sources and evidence grading.
# ---------------------------------------------------------------------------

CAT_PROFILES = {
    "trados": {
        "display_name": "Trados Studio",
        "status": "NOT OFFICIALLY SUPPORTED ON WINE (documented reports)",
        "runtime_expectation": ".NET Framework 4.x (4.8 for recent versions)",
        "known_dependency_failures": [
            "assembly registration failures (fusion)",
            "legacy runtimes for old builds (msvcr70)",
        ],
        "winehq_bugs": [
            "https://bugzilla.winehq.org/show_bug.cgi?id=48152",
            "https://bugzilla.winehq.org/show_bug.cgi?id=13679",
            "https://bugzilla.winehq.org/show_bug.cgi?id=9433",
        ],
    },
    "memoq": {
        "display_name": "memoQ",
        "status": "NOT OFFICIALLY SUPPORTED ON WINE; vendor suggests "
                  "memoQWeb or a virtual machine",
        "runtime_expectation": ".NET Framework 4.x",
        "known_dependency_failures": [
            "startup failures even with .NET preinstalled",
        ],
        "winehq_bugs": [
            "https://bugzilla.winehq.org/show_bug.cgi?id=56929",
        ],
    },
}

CAT_PROFILE_NAMES = tuple(sorted(CAT_PROFILES))


def get_profile(name):
    """Return the profile metadata dict, or ``None`` for unknown names."""
    if not isinstance(name, str):
        return None
    return CAT_PROFILES.get(name.lower())


# ---------------------------------------------------------------------------
# Signature hints: log artifacts observed in CAT tool failure reports.
# Hints are a private triage vocabulary — never stored as error_category.
# ---------------------------------------------------------------------------

_HINT_DOTNET = "dotnet_runtime_missing"
_HINT_ASSEMBLY = "assembly_load_failure"
_HINT_VCRUNTIME = "vc_runtime_missing"
_HINT_LEGACY = "legacy_dll_missing"

# (compiled regex, hint identifier). Patterns are matched against the
# already-capped log text (MAX_LOG_SIZE enforced upstream).
_SIGNATURE_PATTERNS = (
    (re.compile(r"mscoree\.dll|CLR initialization|clr.*failed", re.IGNORECASE),
     _HINT_DOTNET),
    (re.compile(r"0x80131700|\.NET Framework initialization error", re.IGNORECASE),
     _HINT_DOTNET),
    (re.compile(r"fusion\.dll|registration of assembly|Failed to register", re.IGNORECASE),
     _HINT_ASSEMBLY),
    (re.compile(r"msvcp\d+\.dll|vcruntime\d*\.dll|vcredist", re.IGNORECASE),
     _HINT_VCRUNTIME),
    (re.compile(r"msvcr70\.dll|msvcr71\.dll", re.IGNORECASE),
     _HINT_LEGACY),
)

# Static, suggestion-only winetricks verbs. These strings are printed,
# never executed. Users pick the verb matching the actually missing
# component; winetricks is not invoked by this project by design.
WINETRICKS_SUGGESTIONS = {
    _HINT_DOTNET: "winetricks dotnet48",
    _HINT_ASSEMBLY: "winetricks dotnet48",
    _HINT_VCRUNTIME: "winetricks vcrun2005 vcrun2010 vcrun2022",
    _HINT_LEGACY: "winetricks vcrun2003 vcrun2005",
}

# HRESULT/Win32 codes that, when already extracted as generic findings,
# refine to a CAT hint under a profile.
_CODE_HINTS = {
    "0x80131700": _HINT_DOTNET,
    "0xC0000135": _HINT_ASSEMBLY,   # STATUS_DLL_NOT_FOUND: usually mscoree/fusion in CAT reports
    "0x8007007E": _HINT_ASSEMBLY,   # ERROR_MOD_NOT_FOUND
}


def signature_hints(text):
    """Scan capped log text for CAT-specific failure signatures.

    Returns a list of dicts with ``hint``, ``matches``,
    ``winetricks_suggestion`` and ``confidence``. Only hint identifiers,
    aggregate counts and static strings leave this function — the raw
    text and matched substrings do not. Multiple signature patterns
    mapping to the same hint are aggregated into one entry.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    counts = {}
    for pattern, hint in _SIGNATURE_PATTERNS:
        count = len(pattern.findall(text))
        if count:
            counts[hint] = counts.get(hint, 0) + count
    return [
        {
            "hint": hint,
            "matches": count,
            "winetricks_suggestion": WINETRICKS_SUGGESTIONS[hint],
            "confidence": "medium",
        }
        for hint, count in sorted(counts.items())
    ]


def refine_findings(findings, profile_name):
    """Annotate generic extracted findings with CAT-specific hints.

    ``findings`` is the output of
    ``wine_ai.diagnostics.codes.extract_error_codes``. Each finding dict
    is copied (never mutated) and gains ``cat_hint`` and
    ``cat_winetricks_suggestion`` keys (``None`` when no refinement
    applies). ``profile_name`` selects the CAT profile; an unknown or
    missing profile returns unchanged copies.
    """
    profile = get_profile(profile_name)
    if profile is None:
        return [dict(item) for item in (findings or [])]
    refined = []
    for item in findings or []:
        copy = dict(item)
        hint = _CODE_HINTS.get(copy.get("code"))
        if hint is None and copy.get("category") == "missing_module":
            hint = _HINT_ASSEMBLY
        copy["cat_hint"] = hint
        copy["cat_winetricks_suggestion"] = (
            WINETRICKS_SUGGESTIONS[hint] if hint else None
        )
        refined.append(copy)
    return refined
