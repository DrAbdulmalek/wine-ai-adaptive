"""Wine/Windows error-code extraction and classification.

Inspired by the error-code scanning approach of protontool and the
WINEDEBUG channel helper dbgwine, reimplemented from scratch with the
Python standard library only (offline-first, no new dependencies).

The module converts diagnostic log text into *structured* error-code
records. It never returns, logs, or stores raw log text: the output is
limited to code identifiers, their kind, a heuristic error category
(from the KnowledgeStore allow-list) and a confidence level.

Heuristic mapping notice: category mappings below are indicators for
triage, not root-cause proof. Unknown codes are reported with
confidence ``"low"`` instead of being silently dropped or guessed.
"""

import re

MAX_CODES = 20

# 0x-prefixed 32-bit codes: HRESULTs, NTSTATUS values and MSVC/Delphi
# exception codes all share this shape in Wine logs.
HRESULT_RE = re.compile(r"\b0x[0-9A-Fa-f]{8}\b")
NTSTATUS_RE = re.compile(r"\bSTATUS_[A-Z0-9_]{2,48}\b")
WIN32_RE = re.compile(r"\bERROR_[A-Z0-9_]{2,48}\b")

# Wine prints raw return addresses (e.g. 0x7b43bc3d) far more often
# than HRESULTs, so bare 0x codes are only reported when they carry a
# well-known error-shape prefix or an explicit mapping. Everything
# else is treated as noise and ignored. Compared lowercased.
_ERROR_SHAPE_PREFIXES = (
    "0x8007",   # HRESULT_FROM_WIN32(...)
    "0x8013",   # CLR/.NET Framework HRESULT family (initialization, loader)
    "0xc000",   # NTSTATUS severity 0xC...
    "0xc0000",
    "0xe06d",   # MSVC C++ exception
    "0x887a",   # DXGI errors
)

CATEGORY_BY_HRESULT = {
    "0x8007007E": "missing_module",   # ERROR_MOD_NOT_FOUND
    "0x8007007F": "missing_module",   # ERROR_PROC_NOT_FOUND
    "0x8007000E": "memory",           # ERROR_OUTOFMEMORY
    "0x80131700": "startup",          # CLR/.NET Framework initialization failure
    "0xC0000005": "memory",           # STATUS_ACCESS_VIOLATION
    "0xC0000135": "missing_module",   # STATUS_DLL_NOT_FOUND
    "0xC0000139": "missing_module",   # STATUS_ENTRYPOINT_NOT_FOUND
    "0xE06D7363": "startup",          # unhandled MSVC C++ exception
    "0x887A0005": "graphics",         # DXGI_ERROR_DEVICE_REMOVED
    "0x887A0006": "graphics",         # DXGI_ERROR_DEVICE_RESET
}

CATEGORY_BY_NTSTATUS = {
    "STATUS_DLL_NOT_FOUND": "missing_module",
    "STATUS_ENTRYPOINT_NOT_FOUND": "missing_module",
    "STATUS_ACCESS_VIOLATION": "memory",
    "STATUS_INVALID_IMAGE_FORMAT": "graphics",
    "STATUS_NOT_SUPPORTED": "graphics",
}

CATEGORY_BY_WIN32 = {
    "ERROR_MOD_NOT_FOUND": "missing_module",
    "ERROR_PROC_NOT_FOUND": "missing_module",
    "ERROR_NOT_ENOUGH_MEMORY": "memory",
    "ERROR_OUTOFMEMORY": "memory",
}


def classify_hresult(code):
    """Return the heuristic category for an HRESULT, or None."""
    if not isinstance(code, str):
        return None
    if code.lower().startswith("0x"):
        key = "0x" + code[2:].upper()
    else:
        key = code.upper()
    return CATEGORY_BY_HRESULT.get(key)


def classify_ntstatus(name):
    """Return the heuristic category for a STATUS_* name, or None."""
    if not isinstance(name, str):
        return None
    return CATEGORY_BY_NTSTATUS.get(name.upper())


def classify_win32(name):
    """Return the heuristic category for an ERROR_* name, or None."""
    if not isinstance(name, str):
        return None
    return CATEGORY_BY_WIN32.get(name.upper())


def classify_code(code):
    """Classify any known code shape.

    Returns ``(kind, category_or_None)`` where kind is one of
    ``"HRESULT"``, ``"NTSTATUS"`` or ``"WIN32"``.
    """
    if not isinstance(code, str):
        return (None, None)
    upper = code.upper()
    if upper.startswith("0X"):
        return ("HRESULT", classify_hresult(upper))
    if upper.startswith("STATUS_"):
        return ("NTSTATUS", classify_ntstatus(upper))
    if upper.startswith("ERROR_"):
        return ("WIN32", classify_win32(upper))
    return (None, None)


def normalize_code(code, kind):
    """Normalize a code for storage (keeps the ``0x`` prefix lowercase)."""
    if not isinstance(code, str):
        raise ValueError("code must be a string")
    if kind == "HRESULT":
        if not code.lower().startswith("0x"):
            raise ValueError("Invalid HRESULT code")
        return "0x" + code[2:].upper()
    return code.upper()


def extract_error_codes(text, limit=MAX_CODES):
    """Extract structured error codes from diagnostic text.

    Returns a list of dictionaries with the keys ``code``, ``kind``,
    ``category`` and ``confidence``. ``category`` is ``None`` for
    recognised shapes without a mapping; such entries carry
    ``confidence="low"``. Known codes are reported with
    ``confidence="high"``. Duplicates are collapsed, the result is
    capped at ``limit`` entries, and no raw log text is included.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= MAX_CODES
    ):
        raise ValueError(f"limit must be an integer from 1 to {MAX_CODES}")

    found = []
    seen = set()

    def _add(code, kind, category):
        normalized = normalize_code(code, kind)
        if normalized in seen:
            return
        seen.add(normalized)
        found.append({
            "code": normalized,
            "kind": kind,
            "category": category,
            "confidence": "high" if category else "low",
        })

    for match in NTSTATUS_RE.findall(text):
        name = match.upper()
        _add(name, "NTSTATUS", CATEGORY_BY_NTSTATUS.get(name))
        if len(found) >= limit:
            return found[:limit]

    for match in WIN32_RE.findall(text):
        name = match.upper()
        _add(name, "WIN32", CATEGORY_BY_WIN32.get(name))
        if len(found) >= limit:
            return found[:limit]

    for match in HRESULT_RE.findall(text):
        key = "0x" + match[2:].upper()
        category = CATEGORY_BY_HRESULT.get(key)
        if category is None and not match.lower().startswith(
            _ERROR_SHAPE_PREFIXES
        ):
            # Bare return addresses and unrelated hex constants.
            continue
        _add(match, "HRESULT", category)
        if len(found) >= limit:
            break

    return found[:limit]
