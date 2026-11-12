"""Create Bottle is fail-closed until the same-UID mkdir/open race is solved."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.bottle_manager.create import create_bottle
from wine_ai.evidence import EvidenceStatus


class CreateBottleDisabledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def call_create(self, name):
        return create_bottle(name)

    def test_valid_name_is_blocked_without_any_filesystem_write(self):
        info, evidence = self.call_create("safe-bottle")
        self.assertIsNone(info)
        self.assertEqual(evidence[0].status, EvidenceStatus.BLOCKED)
        self.assertEqual(evidence[0].details["feature_state"],
                         "DISABLED_PENDING_SECURITY_DESIGN")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_name_is_also_blocked_without_any_filesystem_write(self):
        info, evidence = self.call_create("../escape")
        self.assertIsNone(info)
        self.assertEqual(evidence[0].status, EvidenceStatus.BLOCKED)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_repeated_calls_do_not_create_audit_or_registry_files(self):
        for name in ("one", "two", "three"):
            info, evidence = self.call_create(name)
            self.assertIsNone(info)
            self.assertEqual(evidence[0].status, EvidenceStatus.BLOCKED)
        self.assertEqual(list(self.home.iterdir()), [])

    # -- unified plan §1.4 named contract tests (2026-11-13) -----------------

    def test_create_blocked_message_contains_doc_url(self):
        # Plan §1.4: the BLOCKED evidence must carry the explicit reason
        # token and the doc_url anchor of the remediation contract.
        info, evidence = self.call_create("safe-bottle")
        details = evidence[0].details
        self.assertEqual(details["reason"],
                         "CREATION_DISABLED_PENDING_SECURITY_REVIEW")
        self.assertEqual(
            details["doc_url"],
            "docs/SECURITY_REMEDIATION_STATUS.md#النقطة-1",
        )
        self.assertIn("SECURITY_REMEDIATION_STATUS", details["doc_url"])

    def test_create_never_returns_success(self):
        # Plan §1.4: create_bottle never returns a record — for valid
        # names, invalid names, and non-string garbage alike.
        for name in ("safe-bottle", "../escape", "", None, 12345,
                     "x" * 100, "-leading-dash"):
            with self.subTest(name=name):
                info, evidence = self.call_create(name)
                self.assertIsNone(info)
                self.assertEqual(len(evidence), 1)
                self.assertEqual(evidence[0].status, EvidenceStatus.BLOCKED)


if __name__ == "__main__":
    unittest.main()
