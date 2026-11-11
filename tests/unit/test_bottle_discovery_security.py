"""Adversarial tests for read-only Wine prefix discovery.

All fixtures are temporary. These tests exercise path traversal, unusual
file types, protected-prefix precedence, race resistance, and scan bounds.
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.bottle_manager import discovery
from wine_ai.bottle_manager.discovery import (
    MAX_SCAN_SUBDIRS,
    discover_prefixes,
    inspect_prefix,
)

def make_prefix(root: Path, name: str = "prefix", arch: str = "win64") -> Path:
    path = root / name
    (path / "drive_c").mkdir(parents=True)
    (path / "system.reg").write_text(f"##arch - {arch}\n", encoding="ascii")
    (path / "user.reg").write_text("## WINE registry\n", encoding="ascii")
    (path / "userdef.reg").write_text("## WINE registry\n", encoding="ascii")
    return path


class BottleDiscoverySecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, clear=False)
        self.env.start()
        os.environ.pop("WINEPREFIX", None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_fifo_system_reg_is_rejected_without_blocking(self):
        prefix = make_prefix(self.home)
        (prefix / "system.reg").unlink()
        os.mkfifo(prefix / "system.reg")
        info, evidence = inspect_prefix(prefix, home=self.home)
        self.assertIsNone(info)
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "system.reg" in str(item.details)
            for item in evidence
        ))

    def test_system_reg_replaced_by_fifo_after_stat_does_not_block(self):
        prefix = make_prefix(self.home)
        directory_fd = discovery._open_dir_path(prefix)
        real_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "system.reg" and kwargs.get("dir_fd") is not None and not swapped:
                (prefix / "system.reg").unlink()
                os.mkfifo(prefix / "system.reg")
                swapped = True
            return real_open(path, flags, *args, **kwargs)

        try:
            with mock.patch.object(discovery.os, "open", side_effect=racing_open):
                architecture, error = discovery._read_reg_arch(directory_fd)
        finally:
            os.close(directory_fd)

        self.assertTrue(swapped)
        self.assertIsNone(architecture)
        self.assertIn("changed identity or type", error)

    def test_symlinked_default_prefix_target_stays_protected(self):
        target = make_prefix(self.home, "real-default")
        (self.home / ".wine").symlink_to(target, target_is_directory=True)
        os.environ["WINEPREFIX"] = str(target)

        prefixes, _ = discover_prefixes(home=self.home)
        matches = [item for item in prefixes if item["path"] == str(target)]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["protected"])

    def test_fifo_drive_c_is_rejected_as_wrong_marker_type(self):
        prefix = make_prefix(self.home)
        (prefix / "drive_c").rmdir()
        os.mkfifo(prefix / "drive_c")
        info, evidence = inspect_prefix(prefix, home=self.home)
        self.assertIsNone(info)
        self.assertTrue(any(item.status.value == "BLOCKED" for item in evidence))

    def test_symlink_in_parent_component_is_rejected(self):
        outside = self.home / "outside"
        managed = outside / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "escape")
        (self.home / ".local").symlink_to(outside, target_is_directory=True)

        prefixes, evidence = discover_prefixes(home=self.home)
        self.assertFalse(any(item["name"] == "escape" for item in prefixes))
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "without following links" in item.summary
            for item in evidence
        ))

    def test_symlinked_nested_syswow64_is_not_trusted(self):
        prefix = make_prefix(self.home, arch="win32")
        windows = prefix / "drive_c" / "windows"
        windows.mkdir()
        target = self.home / "outside-syswow64"
        target.mkdir()
        (windows / "syswow64").symlink_to(target, target_is_directory=True)

        info, evidence = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertIsNone(info["syswow64_present"])
        self.assertIsNone(info["arch_conflict"])
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "Nested Windows architecture path" in item.summary
            for item in evidence
        ))

    def test_wineprefix_equal_to_default_prefix_remains_protected(self):
        prefix = make_prefix(self.home, ".wine")
        os.environ["WINEPREFIX"] = str(prefix)

        prefixes, _ = discover_prefixes(home=self.home)
        matches = [item for item in prefixes if item["path"] == str(prefix)]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["protected"])

    def test_normalized_alias_of_default_prefix_remains_protected(self):
        prefix = make_prefix(self.home, ".wine")
        alias = self.home / "subdir" / ".." / ".wine"
        os.environ["WINEPREFIX"] = str(alias)

        prefixes, _ = discover_prefixes(home=self.home)
        matches = [item for item in prefixes if item["path"] == str(prefix)]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["protected"])

    def test_same_directory_identity_is_deduplicated(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = make_prefix(managed, "same")
        prefixes, _ = discover_prefixes(
            home=self.home,
            candidates=[prefix, prefix / "."],
            container_roots=[managed],
        )
        matches = [item for item in prefixes if item["name"] == "same"]
        self.assertEqual(len(matches), 1)

    def test_directory_swap_to_symlink_is_not_followed(self):
        prefix = make_prefix(self.home)
        outside = make_prefix(self.home, "outside")
        saved = self.home / "prefix.saved"
        real_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "prefix" and kwargs.get("dir_fd") is not None and not swapped:
                prefix.rename(saved)
                prefix.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(discovery.os, "open", side_effect=racing_open):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertTrue(swapped)
        self.assertIsNone(info)
        self.assertTrue(prefix.is_symlink())
        self.assertTrue(any(item.status.value == "BLOCKED" for item in evidence))

    def test_directory_swap_to_different_directory_is_detected(self):
        prefix = make_prefix(self.home)
        saved = self.home / "prefix.saved"
        real_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "prefix" and kwargs.get("dir_fd") is not None and not swapped:
                prefix.rename(saved)
                prefix.mkdir()
                swapped = True
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(discovery.os, "open", side_effect=racing_open):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertTrue(swapped)
        self.assertIsNone(info)
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "changed during open" in str(item.details)
            for item in evidence
        ))

    def test_scan_processes_no_more_than_the_configured_limit(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        for index in range(MAX_SCAN_SUBDIRS + 100):
            (managed / f"item-{index:04d}").mkdir()

        original = discovery._inspect_candidate
        with mock.patch.object(discovery, "_inspect_candidate", wraps=original) as spy:
            _, evidence = discover_prefixes(home=self.home)

        managed_calls = [
            call for call in spy.call_args_list
            if call.args[1] == "managed_root"
        ]
        self.assertLessEqual(len(managed_calls), MAX_SCAN_SUBDIRS)
        self.assertTrue(any(
            item.status.value == "PARTIALLY_PROVEN"
            and "capped at MAX_SCAN_SUBDIRS" in item.summary
            for item in evidence
        ))

    def test_non_regular_registry_marker_is_rejected(self):
        prefix = make_prefix(self.home)
        (prefix / "user.reg").unlink()
        os.mkfifo(prefix / "user.reg")
        info, evidence = inspect_prefix(prefix, home=self.home)
        self.assertIsNone(info)
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "non-regular" in item.summary
            for item in evidence
        ))

    def test_read_only_scan_preserves_file_contents_and_metadata(self):
        prefix = make_prefix(self.home)
        before = {}
        for base, dirs, files in os.walk(self.home, followlinks=False):
            for name in dirs + files:
                path = Path(base) / name
                st = path.lstat()
                before[str(path)] = (
                    stat.S_IFMT(st.st_mode),
                    stat.S_IMODE(st.st_mode),
                    st.st_size,
                    st.st_mtime_ns,
                    path.read_bytes() if stat.S_ISREG(st.st_mode) else None,
                    os.readlink(path) if stat.S_ISLNK(st.st_mode) else None,
                )

        inspect_prefix(prefix, home=self.home)

        after = {}
        for base, dirs, files in os.walk(self.home, followlinks=False):
            for name in dirs + files:
                path = Path(base) / name
                st = path.lstat()
                after[str(path)] = (
                    stat.S_IFMT(st.st_mode),
                    stat.S_IMODE(st.st_mode),
                    st.st_size,
                    st.st_mtime_ns,
                    path.read_bytes() if stat.S_ISREG(st.st_mode) else None,
                    os.readlink(path) if stat.S_ISLNK(st.st_mode) else None,
                )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
