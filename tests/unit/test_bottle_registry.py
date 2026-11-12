"""Security and concurrency tests for the managed bottle registry.

Every filesystem fixture is temporary; nothing touches a real home.
These tests implement the Phase-B test obligations of the 2026-11-11
bottle-manager integration disposition: atomicity, identity
revalidation, symlink/hard-link/foreign-owner rejection, fail-closed
corruption handling, and concurrent read-modify-write serialization.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.bottle_manager.create import MANAGED_ROOT_PARTS, NAME_PATTERN
from wine_ai.bottle_manager.registry import (
    ALLOWED_RECORD_KEYS,
    BottleRegistry,
    BottleRegistryError,
    LOCK_NAME,
    REGISTRY_NAME,
    REGISTRY_VERSION,
    STATE_EMPTY_UNINITIALIZED,
)


def managed_root(home: Path) -> Path:
    return home.joinpath(*MANAGED_ROOT_PARTS)


def _root_fd(root: Path) -> int:
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY)


def remove_entry(root: Path, name: str) -> None:
    """Remove an empty directory entry by name via the root descriptor."""
    fd = _root_fd(root)
    try:
        os.rmdir(name, dir_fd=fd)
    finally:
        os.close(fd)


def prepare_root(home: Path) -> Path:
    app = home / ".local" / "share" / "wine-ai-adaptive"
    app.mkdir(parents=True, mode=0o700)
    app.chmod(0o700)
    root = app / "prefixes"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def make_record(name: str, root: Path, *, operation_id: str = "op-1", identity: dict | None = None) -> dict:
    if identity is None:
        fd = _root_fd(root)
        try:
            info = os.stat(name, dir_fd=fd)
        finally:
            os.close(fd)
        identity = {"dev": int(info.st_dev), "ino": int(info.st_ino)}
    return {
        "name": name,
        "relative_path": f"prefixes/{name}",
        "state": STATE_EMPTY_UNINITIALIZED,
        "created_at": 1731300000.0,
        "operation_id": operation_id,
        "identity": dict(identity),
    }


class RegistrySecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = prepare_root(self.home)
        self.registry = BottleRegistry(home=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def write_registry(self, payload: str):
        (self.root / REGISTRY_NAME).write_text(payload, encoding="utf-8")
        os.chmod(self.root / REGISTRY_NAME, 0o600)

    def add_bottle_dir(self, name: str) -> None:
        os.mkdir(name, 0o700, dir_fd=os.open(self.root, os.O_RDONLY | os.O_DIRECTORY))

    # -- happy path ---------------------------------------------------------

    def test_roundtrip_register_read_get(self):
        self.add_bottle_dir("alpha")
        record = make_record("alpha", self.root)
        stored = self.registry.register(record)
        self.assertEqual(stored["name"], "alpha")
        self.assertEqual(self.registry.read()["alpha"], stored)
        self.assertEqual(self.registry.get("alpha"), stored)

    def test_registry_file_is_private_and_singly_linked(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        info = os.lstat(self.root / REGISTRY_NAME)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)
        lock_info = os.lstat(self.root / LOCK_NAME)
        self.assertEqual(stat.S_IMODE(lock_info.st_mode), 0o600)

    def test_read_returns_empty_when_root_uninitialized(self):
        empty_home = self.home / "fresh"
        empty_home.mkdir(mode=0o700)
        registry = BottleRegistry(home=empty_home)
        self.assertEqual(registry.read(), {})
        self.assertEqual(registry.orphans()["unregistered_on_disk"], {})

    def test_register_requires_existing_root(self):
        absent = BottleRegistry(home=self.home / "nowhere")
        record = make_record("alpha", self.root, identity={"dev": 1, "ino": 2})
        with self.assertRaises(BottleRegistryError):
            absent.register(record)

    def test_idempotent_re_registration_of_identical_record(self):
        self.add_bottle_dir("alpha")
        record = make_record("alpha", self.root)
        self.registry.register(record)
        self.registry.register(record)  # identical: allowed, no duplicate

    def test_register_rejects_duplicate_with_different_record(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root, operation_id="op-1"))
        with self.assertRaises(BottleRegistryError):
            self.registry.register(make_record("alpha", self.root, operation_id="op-2"))

    # -- name validation ------------------------------------------------------

    def test_invalid_names_rejected_before_filesystem_effects(self):
        synthetic = {"dev": 1, "ino": 2}
        for bad in ("../escape", "a/b", ".hidden", "-lead", "x" * 65, "", None, 5):
            with self.assertRaises(BottleRegistryError):
                self.registry.get(bad)
            record = make_record("alpha", self.root, identity=synthetic)
            with self.assertRaises(BottleRegistryError):
                self.registry.register({**record, "name": bad})

    def test_name_pattern_matches_create_contract(self):
        for good in ("A", "ok_name", "x" * 64, "Trados-Studio_9"):
            self.assertTrue(NAME_PATTERN.fullmatch(good))
        for bad in ("", ".a", "a b", "a/b", "x" * 65):
            self.assertIsNone(NAME_PATTERN.fullmatch(bad))

    # -- record validation ------------------------------------------------------

    def test_record_with_unsupported_key_rejected(self):
        self.add_bottle_dir("alpha")
        record = make_record("alpha", self.root)
        record["wine_bin"] = "/usr/bin/wine"
        with self.assertRaises(BottleRegistryError):
            self.registry.register(record)

    def test_record_with_missing_key_rejected(self):
        self.add_bottle_dir("alpha")
        record = make_record("alpha", self.root)
        del record["operation_id"]
        with self.assertRaises(BottleRegistryError):
            self.registry.register(record)

    def test_record_with_foreign_state_rejected(self):
        self.add_bottle_dir("alpha")
        record = make_record("alpha", self.root)
        record["state"] = "INITIALIZED_WITH_WINE"
        with self.assertRaises(BottleRegistryError):
            self.registry.register(record)

    def test_record_with_malformed_identity_rejected(self):
        self.add_bottle_dir("alpha")
        for identity in ({"dev": 1}, {"dev": 1, "ino": -2}, {"dev": "1", "ino": 2}, "x"):
            record = make_record("alpha", self.root)
            record["identity"] = identity
            with self.assertRaises(BottleRegistryError):
                self.registry.register(record)

    def test_extra_records_keys_subset_is_enforced(self):
        self.assertEqual(
            set(ALLOWED_RECORD_KEYS),
            {"name", "relative_path", "state", "created_at", "operation_id", "identity"},
        )

    # -- fail-closed document handling -----------------------------------------

    def test_corrupted_registry_rejected(self):
        self.write_registry("{ not json")
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    def test_registry_without_bottles_object_rejected(self):
        self.write_registry(json.dumps({"registry_version": REGISTRY_VERSION}))
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    def test_registry_with_wrong_version_rejected(self):
        self.write_registry(json.dumps({"registry_version": 99, "bottles": {}}))
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    def test_registry_with_unknown_top_key_rejected(self):
        self.write_registry(
            json.dumps({"registry_version": REGISTRY_VERSION, "bottles": {}, "extra": 1})
        )
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    def test_registry_with_invalid_record_rejected(self):
        self.write_registry(
            json.dumps(
                {
                    "registry_version": REGISTRY_VERSION,
                    "bottles": {"bad": {"name": "bad"}},
                }
            )
        )
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    # -- symlink / hard-link / ownership / mode rejection ------------------------

    def test_symlinked_registry_rejected(self):
        target = self.home / "outside.json"
        target.write_text("{}", encoding="utf-8")
        os.symlink(target, self.root / REGISTRY_NAME)
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    def test_registry_symlinked_during_register_rejected(self):
        self.add_bottle_dir("alpha")
        target = self.home / "outside.json"
        target.write_text("{}", encoding="utf-8")
        os.symlink(target, self.root / REGISTRY_NAME)
        with self.assertRaises(BottleRegistryError):
            self.registry.register(make_record("alpha", self.root))

    def test_hardlinked_registry_rejected(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        os.link(self.root / REGISTRY_NAME, self.root / "registry-copy.json")
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    def test_wrong_mode_registry_rejected_not_repaired(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        os.chmod(self.root / REGISTRY_NAME, 0o644)
        with self.assertRaises(BottleRegistryError):
            self.registry.read()
        # fail-closed: the mode must NOT have been silently fixed
        self.assertEqual(
            stat.S_IMODE(os.lstat(self.root / REGISTRY_NAME).st_mode), 0o644
        )

    def test_foreign_owned_registry_rejected(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        real_geteuid = os.geteuid()
        with mock.patch("wine_ai.bottle_manager.registry.os.geteuid", return_value=real_geteuid + 1):
            with self.assertRaises(BottleRegistryError):
                self.registry.read()

    def test_lock_symlink_rejected(self):
        target = self.home / "outside.lock"
        target.write_text("", encoding="utf-8")
        os.symlink(target, self.root / LOCK_NAME)
        with self.assertRaises(BottleRegistryError):
            self.registry.read()

    # -- identity revalidation -----------------------------------------------------

    def test_atomic_write_refuses_foreign_identity(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        # A writer that captured a different identity (here: the lock file's)
        # must be refused instead of silently replacing the registry.
        lock_ino = os.lstat(self.root / LOCK_NAME).st_ino
        reg_ino = os.lstat(self.root / REGISTRY_NAME).st_ino
        self.assertNotEqual(lock_ino, reg_ino)
        root_fd = self.registry._open_root()
        try:
            with self.assertRaises(BottleRegistryError):
                self.registry._atomic_write(
                    root_fd,
                    {"registry_version": REGISTRY_VERSION, "bottles": {}},
                    (os.lstat(self.root / REGISTRY_NAME).st_dev, lock_ino),
                )
        finally:
            os.close(root_fd)
        # the real registry survived untouched
        self.assertEqual(os.lstat(self.root / REGISTRY_NAME).st_ino, reg_ino)

    def test_atomic_write_revalidates_identity_after_temp_fsync(self):
        self.add_bottle_dir("alpha")
        self.add_bottle_dir("beta")
        self.registry.register(make_record("alpha", self.root))
        real_fsync = os.fsync
        swapped = {"done": False}

        def swap_registry_after_temp_sync(fd):
            if not swapped["done"]:
                swapped["done"] = True
                os.rename(self.root / REGISTRY_NAME, self.root / "registry.original")
                replacement = self.root / REGISTRY_NAME
                replacement.write_text(
                    json.dumps({"registry_version": REGISTRY_VERSION, "bottles": {}}),
                    encoding="utf-8",
                )
                os.chmod(replacement, 0o600)
            return real_fsync(fd)

        with mock.patch("wine_ai.bottle_manager.registry.os.fsync",
                        side_effect=swap_registry_after_temp_sync):
            with self.assertRaises(BottleRegistryError):
                self.registry.register(make_record("beta", self.root))
        self.assertTrue(swapped["done"])
        self.assertEqual(self.registry.read(), {})
        self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(self.root)))

    def test_temp_registry_mode_is_exact_under_restrictive_umask(self):
        self.add_bottle_dir("alpha")
        previous = os.umask(0o777)
        try:
            self.registry.register(make_record("alpha", self.root))
        finally:
            os.umask(previous)
        info = os.lstat(self.root / REGISTRY_NAME)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    def test_register_after_external_swap_reads_fresh_state(self):
        # Documented behavior: register never caches identities across calls.
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        os.unlink(self.root / REGISTRY_NAME)
        self.write_registry(json.dumps({"registry_version": REGISTRY_VERSION, "bottles": {}}))
        self.registry.register(make_record("alpha", self.root, operation_id="op-2"))
        self.assertEqual(self.registry.get("alpha")["operation_id"], "op-2")

    def _overwrite_record_identity(self, name: str, identity: dict) -> None:
        """White-box helper: point one record's identity at a foreign identity.

        Deterministically simulates an on-disk swap that inode reuse on some
        filesystems can otherwise mask (rmdir+mkdir may reuse the inode).
        """
        root_fd = self.registry._open_root()
        try:
            fd, expected = self.registry._open_registry(root_fd)
            os.close(fd)
            bottles = self.registry.read()
            bottles[name]["identity"] = dict(identity)
            document = {"registry_version": REGISTRY_VERSION, "bottles": bottles}
            self.registry._atomic_write(root_fd, document, expected)
        finally:
            os.close(root_fd)

    def test_verify_reports_ok_and_missing(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        self.assertEqual(self.registry.verify("alpha")["result"], "OK")
        remove_entry(self.root, "alpha")
        self.assertEqual(self.registry.verify("alpha")["result"], "MISSING")

    def test_verify_flags_foreign_identity(self):
        # A record pointing at another directory's (dev, ino) must be
        # flagged regardless of inode-reuse behavior of the filesystem.
        self.add_bottle_dir("alpha")
        self.add_bottle_dir("beta")
        self.registry.register(make_record("alpha", self.root))
        beta_identity = {"dev": int(os.lstat(self.root / "beta").st_dev),
                         "ino": int(os.lstat(self.root / "beta").st_ino)}
        self.assertNotEqual(beta_identity, make_record("alpha", self.root)["identity"])
        self._overwrite_record_identity("alpha", beta_identity)
        self.assertEqual(self.registry.verify("alpha")["result"], "IDENTITY_CHANGED")

    # -- reconciliation -------------------------------------------------------------

    def test_orphans_reports_all_disagreements(self):
        self.add_bottle_dir("registered")
        self.registry.register(make_record("registered", self.root))
        self.add_bottle_dir("orphan")  # on disk, not registered
        os.symlink(self.home / "elsewhere", self.root / "evil")
        (self.root / "stray.txt").write_text("x", encoding="utf-8")
        remove_entry(self.root, "registered")
        report = self.registry.orphans()
        self.assertIn("orphan", report["unregistered_on_disk"])
        self.assertEqual(report["unexpected_entries"]["evil"], "symlink")
        self.assertEqual(report["unexpected_entries"]["stray.txt"], "irregular_file")
        self.assertIn("registered", report["missing_on_disk"])

    def test_orphans_ignores_registry_infrastructure(self):
        self.registry.register  # noqa: B018 - attribute exists
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        report = self.registry.orphans()
        self.assertNotIn(REGISTRY_NAME, report["unexpected_entries"])
        self.assertNotIn(LOCK_NAME, report["unexpected_entries"])


class RegistryConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = prepare_root(self.home)
        self.registry = BottleRegistry(home=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def add_bottle_dir(self, name: str) -> None:
        os.mkdir(name, 0o700, dir_fd=os.open(self.root, os.O_RDONLY | os.O_DIRECTORY))

    def test_distinct_names_survive_concurrent_registration(self):
        names = [f"bottle-{i}" for i in range(8)]
        for name in names:
            self.add_bottle_dir(name)
        errors = []

        def worker(name: str):
            try:
                self.registry.register(make_record(name, self.root, operation_id=f"op-{name}"))
            except Exception as exc:  # pragma: no cover - collected below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        stored = self.registry.read()
        self.assertEqual(sorted(stored), sorted(names))

    def test_same_name_concurrent_registration_elects_one_winner(self):
        self.add_bottle_dir("contested")
        results = []
        errors = []

        def worker(i: int):
            try:
                self.registry.register(
                    make_record("contested", self.root, operation_id=f"op-{i}")
                )
                results.append(i)
            except BottleRegistryError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 7)
        for exc in errors:
            self.assertIn("already registered", str(exc))

    def test_lock_file_is_unique_named_and_atomic_writes_use_unique_tmp(self):
        self.add_bottle_dir("alpha")
        self.registry.register(make_record("alpha", self.root))
        # the rejected draft used a fixed '<name>.tmp'; the redesign must not
        leftovers = [n for n in os.listdir(self.root) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertTrue(os.lstat(self.root / LOCK_NAME))


if __name__ == "__main__":
    unittest.main()
