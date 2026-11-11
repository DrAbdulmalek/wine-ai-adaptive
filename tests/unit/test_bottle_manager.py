"""Tests for the safe BottleManager facade (Phase-B rebuild).

Implements the mandatory Phase-E test obligations of the 2026-11-11
integration disposition that apply to the implemented scope:

- concurrent creation of the same name elects exactly one winner;
- concurrent creation of different names registers every bottle;
- identity swaps are detected (never repaired);
- symlink attacks on the managed tree are rejected;
- orphaned directories are reported, never removed;
- creation never invokes Wine or any subprocess;
- the facade has no delete/run/initialize capability at all.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import wine_ai.bottle_manager.manager as manager_module
from wine_ai.bottle_manager.create import (
    AUDIT_DIR_PARTS,
    AUDIT_FILENAME,
    MANAGED_ROOT_PARTS,
)
from wine_ai.bottle_manager.manager import BottleManager
from wine_ai.bottle_manager.registry import REGISTRY_NAME, STATE_EMPTY_UNINITIALIZED
from wine_ai.evidence import EvidenceStatus


def audit_file(home: Path) -> Path:
    return home.joinpath(*AUDIT_DIR_PARTS, AUDIT_FILENAME)


class BottleManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patcher = mock.patch(
            "wine_ai.bottle_manager.create.Path.home", return_value=self.home
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = BottleManager()

    def tearDown(self):
        self.tmp.cleanup()

    def root(self) -> Path:
        return self.home.joinpath(*MANAGED_ROOT_PARTS)

    def audit_records(self):
        path = audit_file(self.home)
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    # -- creation flow -------------------------------------------------------

    def test_create_registers_identity_pinned_empty_bottle(self):
        record, evidences = self.manager.create_bottle("alpha")
        self.assertIsNotNone(record)
        self.assertEqual(record["state"], STATE_EMPTY_UNINITIALIZED)
        self.assertEqual(record["name"], "alpha")
        self.assertIn("identity", record)
        info = os.stat(record["name"], dir_fd=os.open(self.root(), os.O_RDONLY | os.O_DIRECTORY))
        self.assertEqual(record["identity"], {"dev": int(info.st_dev), "ino": int(info.st_ino)})
        self.assertTrue(
            all(e.status == EvidenceStatus.PROVEN for e in evidences), str(evidences)
        )
        registered = self.manager.get_bottle("alpha")
        self.assertEqual(registered["name"], record["name"])
        self.assertEqual(registered["identity"], record["identity"])
        self.assertEqual(registered["state"], record["state"])
        self.assertEqual(registered["operation_id"], record["operation_id"])
        self.assertEqual(registered["relative_path"], "prefixes/alpha")
        target = self.root() / "alpha"
        self.assertEqual(stat.S_IMODE(os.lstat(target).st_mode), 0o700)
        self.assertEqual(os.lstat(target).st_uid, os.geteuid())
        self.assertEqual(
            sorted(os.listdir(os.open(target, os.O_RDONLY | os.O_DIRECTORY))), []
        )
        outcomes = [r["outcome"] for r in self.audit_records()]
        self.assertIn("PROVEN", outcomes)

    def test_create_refuses_duplicate_name(self):
        first, _ = self.manager.create_bottle("alpha")
        self.assertIsNotNone(first)
        second, evidences = self.manager.create_bottle("alpha")
        self.assertIsNone(second)
        self.assertTrue(
            any(e.status == EvidenceStatus.BLOCKED for e in evidences)
        )
        self.assertEqual(sorted(self.manager.list_bottles()), ["alpha"])

    def test_invalid_name_is_blocked_without_registration(self):
        record, evidences = self.manager.create_bottle("../escape")
        self.assertIsNone(record)
        self.assertTrue(
            any(e.status == EvidenceStatus.BLOCKED for e in evidences)
        )
        self.assertEqual(self.manager.list_bottles(), {})
        outcomes = [r["outcome"] for r in self.audit_records()]
        self.assertIn("BLOCKED", outcomes)

    def test_get_unknown_bottle_raises(self):
        with self.assertRaises(Exception):
            self.manager.get_bottle("ghost")

    def test_root_path_is_bound_to_home(self):
        self.assertEqual(self.manager.root_path, str(self.root()))

    # -- reconciliation -------------------------------------------------------

    def _overwrite_record_identity(self, name: str, identity: dict) -> None:
        """White-box helper: point one record's identity at a foreign identity.

        Deterministically simulates an on-disk swap; a real rmdir+mkdir swap
        can be masked by inode reuse on some filesystems (observed on CI).
        """
        from wine_ai.bottle_manager.registry import REGISTRY_VERSION

        registry = self.manager._registry
        root_fd = registry._open_root()
        try:
            fd, expected = registry._open_registry(root_fd)
            os.close(fd)
            bottles = registry.read()
            bottles[name]["identity"] = dict(identity)
            document = {"registry_version": REGISTRY_VERSION, "bottles": bottles}
            registry._atomic_write(root_fd, document, expected)
        finally:
            os.close(root_fd)

    def test_verify_flags_tampered_identity(self):
        self.manager.create_bottle("alpha")
        self.manager.create_bottle("beta")
        self.assertEqual(self.manager.verify_bottle("alpha")["result"], "OK")
        # Point alpha's registry record at beta's identity: the recorded
        # (dev, ino) no longer matches the on-disk directory.
        beta_identity = self.manager.get_bottle("beta")["identity"]
        self.assertNotEqual(beta_identity, self.manager.get_bottle("alpha")["identity"])
        self._overwrite_record_identity("alpha", beta_identity)
        self.assertEqual(self.manager.verify_bottle("alpha")["result"], "IDENTITY_CHANGED")

    def test_verify_detects_missing_directory(self):
        self.manager.create_bottle("alpha")
        fd = os.open(self.root(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.rmdir("alpha", dir_fd=fd)
        finally:
            os.close(fd)
        self.assertEqual(self.manager.verify_bottle("alpha")["result"], "MISSING")

    def test_verify_detects_symlink_replacement(self):
        self.manager.create_bottle("alpha")
        fd = os.open(self.root(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.rmdir("alpha", dir_fd=fd)
        finally:
            os.close(fd)
        os.symlink(self.home, self.root() / "alpha")
        self.assertEqual(self.manager.verify_bottle("alpha")["result"], "SYMLINK")

    def test_verify_detects_mode_change(self):
        self.manager.create_bottle("alpha")
        os.chmod(self.root() / "alpha", 0o755)
        self.assertEqual(self.manager.verify_bottle("alpha")["result"], "MODE_CHANGED")

    def test_orphans_reports_unregistered_and_missing(self):
        self.manager.create_bottle("kept")
        os.mkdir(self.root() / "orphan", 0o700)
        report = self.manager.orphans()
        self.assertIn("orphan", report["unregistered_on_disk"])
        self.assertNotIn("kept", report["unregistered_on_disk"])
        self.assertNotIn(REGISTRY_NAME, report["unexpected_entries"])

    # -- honest partial-failure handling ---------------------------------------

    def test_registration_failure_reports_blocked_orphan(self):
        from wine_ai.bottle_manager.registry import BottleRegistryError

        with mock.patch(
            "wine_ai.bottle_manager.registry.BottleRegistry.register",
            side_effect=BottleRegistryError("injected failure"),
        ):
            record, evidences = self.manager.create_bottle("alpha")
        self.assertIsNone(record)
        blocked = [e for e in evidences if e.status == EvidenceStatus.BLOCKED]
        self.assertTrue(blocked)
        self.assertIn("unregistered", blocked[0].summary)
        # the directory exists but is unregistered: reported, never deleted
        report = self.manager.orphans()
        self.assertIn("alpha", report["unregistered_on_disk"])

    def test_identity_capture_failure_reports_blocked(self):
        from wine_ai.bottle_manager.registry import BottleRegistryError

        with mock.patch(
            "wine_ai.bottle_manager.registry.BottleRegistry.pin_identity",
            side_effect=BottleRegistryError("injected pin failure"),
        ):
            record, evidences = self.manager.create_bottle("alpha")
        self.assertIsNone(record)
        blocked = [e for e in evidences if e.status == EvidenceStatus.BLOCKED]
        self.assertTrue(blocked)
        self.assertIn("unregistered", blocked[0].summary)

    # -- concurrency (Phase E) ---------------------------------------------------

    def test_concurrent_same_name_elects_one_winner(self):
        results = []
        blocked = []
        lock = threading.Lock()

        def worker():
            record, evidences = self.manager.create_bottle("contested")
            with lock:
                if record is not None:
                    results.append(record)
                else:
                    blocked.append(evidences)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(blocked), 5)
        self.assertEqual(sorted(self.manager.list_bottles()), ["contested"])

    def test_concurrent_different_names_all_register(self):
        names = [f"bottle-{i}" for i in range(5)]
        errors = []

        def worker(name: str):
            record, evidences = self.manager.create_bottle(name)
            if record is None:
                errors.append(evidences)

        threads = [threading.Thread(target=worker, args=(n,)) for n in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.manager.list_bottles()), sorted(names))
        for name in names:
            self.assertEqual(self.manager.verify_bottle(name)["result"], "OK")

    # -- capability boundaries (Phase E) -------------------------------------------

    def test_no_execution_no_deletion_capability(self):
        for forbidden in (
            "run_exe",
            "run_command",
            "delete_bottle",
            "remove_bottle",
            "initialize_bottle",
            "wineboot",
            "install_recipe",
            "apply_recipe",
        ):
            self.assertFalse(hasattr(BottleManager, forbidden), forbidden)
        # AST-level tripwire: no subprocess/shutil imports or references in
        # the facade (docstring mentions are documentation, not code).
        tree = ast.parse(inspect.getsource(manager_module))
        forbidden_modules = {"subprocess", "shutil"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                self.assertEqual(names & forbidden_modules, set(), ast.dump(node))
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden_modules, node.module)
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"rmtree", "system", "popen", "call", "run", "check_output"})

    def test_no_subprocess_is_ever_invoked(self):
        with mock.patch.object(
            subprocess, "run", side_effect=AssertionError("subprocess must not be used")
        ), mock.patch.object(
            subprocess, "Popen", side_effect=AssertionError("subprocess must not be used")
        ):
            self.manager.create_bottle("quiet")
            self.manager.list_bottles()
            self.manager.get_bottle("quiet")
            self.manager.verify_bottle("quiet")
            self.manager.orphans()
        self.assertEqual(sorted(self.manager.list_bottles()), ["quiet"])


if __name__ == "__main__":
    unittest.main()
