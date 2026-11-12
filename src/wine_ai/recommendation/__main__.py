"""Executable stub entry: python -m wine_ai.recommendation.

Reports NO_PROVEN_EVIDENCE per the §2.3 contract — see status.py and
docs/design/recommendation-trust-anchor.md.
"""

from wine_ai.recommendation.status import main

if __name__ == "__main__":
    raise SystemExit(main())
