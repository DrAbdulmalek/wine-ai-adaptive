"""Read-only BottleManager facade tests; Create Bottle is intentionally disabled."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.bottle_manager.manager import BottleManager
from wine_ai.bottle_manager.create import MANAGED_ROOT_PARTS
from wine_ai.bottle_manager.registry import (
    STATE_EMPTY_UNINITIALIZED, BottleRegistry,
)
from wine_ai.evidence import EvidenceStatus


class BottleManagerReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patcher = mock.patch("wine_ai.bottle_manager.manager.Path.home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = BottleManager()

    def tearDown(self):
        self.tmp.cleanup()

    def root(self):
        return self.home.joinpath(*MANAGED_ROOT_PARTS)

    def prepare_root(self):
        self.root().mkdir(parents=True, mode=0o700)
        os.chmod(self.root(), 0o700)

    def test_create_is_blocked_without_filesystem_side_effects(self):
        record, evidence = self.manager.create_bottle("alpha")
        self.assertIsNone(record)
        self.assertEqual(evidence[0].status, EvidenceStatus.BLOCKED)
        self.assertFalse(self.root().exists())
        self.assertFalse((self.home / ".local" / "share" / "wine-ai-adaptive" /
                          "logs" / "create-bottle.jsonl").exists())

    def test_invalid_create_is_blocked(self):
        record, evidence = self.manager.create_bottle("../escape")
        self.assertIsNone(record)
        self.assertEqual(evidence[0].status, EvidenceStatus.BLOCKED)
        self.assertFalse(self.root().exists())

    def test_read_only_empty_registry_operations(self):
        self.prepare_root()
        self.assertEqual(self.manager.list_bottles(), {})
        self.assertEqual(self.manager.orphans()["unregistered_on_disk"], {})
        self.assertFalse((self.root() / "registry.lock").exists())
        with self.assertRaises(Exception):
            self.manager.get_bottle("missing")

    def test_read_only_facade_verifies_existing_registered_directory(self):
        self.prepare_root()
        (self.root() / "alpha").mkdir(mode=0o700)
        info = os.stat(self.root() / "alpha", follow_symlinks=False)
        record = {
            "name": "alpha",
            "relative_path": "prefixes/alpha",
            "state": STATE_EMPTY_UNINITIALIZED,
            "created_at": 1731300000.0,
            "operation_id": "test-op",
            "identity": {"dev": int(info.st_dev), "ino": int(info.st_ino)},
        }
        BottleRegistry(home=self.home).register(record)
        self.assertEqual(self.manager.list_bottles()["alpha"], record)
        self.assertEqual(self.manager.get_bottle("alpha"), record)
        self.assertEqual(self.manager.verify_bottle("alpha")["result"], "OK")

    def test_orphans_are_reported_without_cleanup(self):
        self.prepare_root()
        (self.root() / "unregistered").mkdir(mode=0o700)
        before = sorted(item.name for item in self.root().iterdir())
        report = self.manager.orphans()
        after = sorted(item.name for item in self.root().iterdir())
        self.assertIn("unregistered", report["unregistered_on_disk"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
