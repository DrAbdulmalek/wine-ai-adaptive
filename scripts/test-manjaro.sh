#!/usr/bin/env bash
# Manjaro smoke test — FAIL-LOUD variant (review round 2026-11-13).
#
# Run INSIDE a clean Manjaro VM/host against a built wheel. Every step's
# result is recorded; ANY failure makes the script exit non-zero and the
# final verdict line is never printed. Success is claimed only after all
# steps pass — matching SECURITY_REMEDIATION_STATUS.md section 5.
#
# Usage:  ./scripts/test-manjaro.sh /path/to/wine_ai_adaptive-<ver>.whl
#
# The final report is written to ./manjaro-smoke-report.txt and printed.

set -euo pipefail
umask 077  # nothing this script creates is world-readable (reviewer round 2026-11-13)

WHEEL="${1:-}"
if [[ -z "$WHEEL" || ! -f "$WHEEL" ]]; then
    echo "USAGE: $0 <path-to-wheel>" >&2
    exit 64
fi
WHEEL="$(readlink -f "$WHEEL")"

REPORT="$(pwd)/manjaro-smoke-report.txt"
: > "$REPORT"

STEP=0
FAILED=0

record() {  # record <STATUS: PASS|FAIL|INFO> <step name> [detail]
    local status="$1" name="$2" detail="${3:-}"
    STEP=$((STEP + 1))
    printf '%02d  %-5s  %s%s\n' "$STEP" "$status" "$name" \
        "${detail:+ — $detail}" | tee -a "$REPORT"
    if [[ "$status" == "FAIL" ]]; then
        FAILED=$((FAILED + 1))
    fi
}

run_step() {  # run_step <step name> <command...>
    local name="$1"; shift
    local out rc
    if out="$("$@" 2>&1)"; then
        record "PASS" "$name"
        if [[ -n "$out" ]]; then
            printf '      %s\n' "$out" | head -5 | tee -a "$REPORT" || true
        fi
    else
        rc=$?
        record "FAIL" "$name" "exit=$rc"
        printf '      --- output ---\n%s\n      --------------\n' "$out" \
            | head -20 | tee -a "$REPORT"
    fi
}

wine_identity() {  # portable identity snapshot of ~/.wine (or "absent")
    if [[ -d "${HOME}/.wine" ]]; then
        stat -Lc '%d:%i:%Y' "${HOME}/.wine"
    else
        printf 'absent'
    fi
}

VENV="$(mktemp -d /tmp/manjaro-smoke.XXXXXX)"
trap 'rm -rf "$VENV"' EXIT

record "INFO" "wheel" "$(basename "$WHEEL")"
record "INFO" "host" "$(grep -E '^(NAME|VERSION)=' /etc/os-release 2>/dev/null | tr '\n' ' ' || true)"
record "INFO" "kernel" "$(uname -r)"
record "INFO" "machine-id" "$(sh -c 'test -s /etc/machine-id && echo present || echo missing')"

# Snapshot ~/.wine identity BEFORE any CLI run (section 5, check 5).
SNAP_BEFORE="$(wine_identity)"
record "INFO" "~/.wine snapshot (before)" "$SNAP_BEFORE"

# Step 1: clean venv + wheel install. The package is stdlib-only by
# policy, so --no-index must succeed: any dependency resolution attempt
# (network) is a real packaging defect, not a soft warning.
run_step "create venv"        python -m venv "$VENV/venv"
run_step "install wheel"      "$VENV/venv/bin/pip" install --no-input --no-index "$WHEEL"
run_step "entry point exists" test -x "$VENV/venv/bin/wine-ai"

# Step 2: read-only CLI surface.
run_step "wine-ai --help"     "$VENV/venv/bin/wine-ai" --help
run_step "wine-ai status"     "$VENV/venv/bin/wine-ai" status
run_step "wine-ai prefixes"   "$VENV/venv/bin/wine-ai" prefixes

# Step 3: graceful Vulkan probe on a clean VM.
if command -v vulkaninfo >/dev/null 2>&1; then
    record "INFO" "vulkaninfo" "present; GPU probe result is in status output above"
else
    record "INFO" "vulkaninfo" "absent; UNPROVEN probe output expected (graceful)"
fi

# Step 4: verify ~/.wine was not mutated by any of the CLI runs.
SNAP_AFTER="$(wine_identity)"
if [[ "$SNAP_AFTER" == "$SNAP_BEFORE" ]]; then
    record "PASS" "no ~/.wine mutation"
else
    record "FAIL" "no ~/.wine mutation" "$SNAP_BEFORE -> $SNAP_AFTER"
fi

# Step 5: run the stdlib unit suite if a source checkout sits next to
# the wheel (optional on VMs that carry only the wheel).
WHEEL_DIR="$(dirname "$WHEEL")"
if [[ -d "$WHEEL_DIR/src" && -d "$WHEEL_DIR/tests/unit" ]]; then
    run_step "unit suite (CI-mirrored)" env PYTHONPATH="$WHEEL_DIR/src" \
        python3 -m unittest discover -s "$WHEEL_DIR/tests/unit" -p 'test_*.py'
else
    record "INFO" "unit suite" "skipped: no source checkout next to the wheel"
fi

printf '\n===== SUMMARY =====\n' | tee -a "$REPORT"
printf 'steps: %d   failures: %d\n' "$STEP" "$FAILED" | tee -a "$REPORT"

if [[ "$FAILED" -eq 0 ]]; then
    printf 'MANJARO_SMOKE_OK  (%s)\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$REPORT"
    exit 0
fi
printf 'MANJARO_SMOKE_FAILED — see %s\n' "$REPORT" >&2
exit 1
