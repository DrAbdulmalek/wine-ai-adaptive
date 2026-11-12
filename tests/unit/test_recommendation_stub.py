"""Contract tests for the recommendation stub (unified plan §2.3).

The engine does not exist yet (deferred until a real trust anchor is
decided). These tests pin the ONLY behavior the stub may ever have:
a NO_PROVEN_EVIDENCE payload that says so explicitly and points at the
remediation contract — no guessing, no PROVEN reliance.
"""

import io
import json
import unittest
from contextlib import redirect_stdout

from wine_ai import recommendation


class RecommendationStubContractTests(unittest.TestCase):
    def test_status_is_no_proven_evidence(self):
        payload = recommendation.build_status_payload()
        self.assertEqual(payload["status"], "NO_PROVEN_EVIDENCE")

    def test_warnings_reference_the_remediation_contract(self):
        payload = recommendation.build_status_payload()
        self.assertTrue(payload["warnings"], "warnings must not be empty")
        joined = " ".join(payload["warnings"])
        self.assertIn("PROVEN writes are blocked", joined)
        self.assertIn("docs/SECURITY_REMEDIATION_STATUS.md", joined)
        self.assertIn("expected behavior", joined)

    def test_main_prints_valid_json_and_exits_zero(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = recommendation.main()
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["status"], "NO_PROVEN_EVIDENCE")
        self.assertEqual(payload, recommendation.build_status_payload())

    def test_payload_shape_matches_plan_2_3(self):
        # The plan's §2.3 contract: exactly two keys, status fixed.
        payload = recommendation.build_status_payload()
        self.assertEqual(set(payload), {"status", "warnings"})
        self.assertIsInstance(payload["warnings"], list)


if __name__ == "__main__":
    unittest.main()
