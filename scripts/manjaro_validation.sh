#!/usr/bin/env bash
# Unified plan §5.3 names scripts/manjaro_validation.sh; the implemented,
# live-tested fail-loud validator is scripts/test-manjaro.sh (this round's
# item 13 disposition, recorded in docs/STATUS.md). This shim exists so
# the plan's documented entry point works verbatim:
#
#   ./scripts/manjaro_validation.sh /path/to/wine_ai_adaptive-<ver>.whl
#
# Semantics are test-manjaro.sh's: set -euo pipefail, every step recorded,
# any failure -> non-zero exit and the OK marker is never printed.
set -euo pipefail
exec "$(dirname "$0")/test-manjaro.sh" "$@"
