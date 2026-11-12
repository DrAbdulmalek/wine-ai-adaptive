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


if __name__ == "__main__":
    unittest.main()
