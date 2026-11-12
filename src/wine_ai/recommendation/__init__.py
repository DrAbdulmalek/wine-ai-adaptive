"""Recommendation contract stub — read-only, no engine.

The engine is deferred (unified plan §3, 2026-11-13) until a real
trust anchor exists. Only the NO_PROVEN_EVIDENCE contract lives here;
the executable entry point is ``python -m wine_ai.recommendation``.
"""

from wine_ai.recommendation.status import (
    STATUS,
    WARNINGS,
    build_status_payload,
    main,
)

__all__ = ["STATUS", "WARNINGS", "build_status_payload", "main"]
