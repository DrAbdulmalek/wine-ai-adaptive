"""Adversarial tests for read-only Wine prefix discovery.

All fixtures are temporary. These tests exercise path traversal, unusual
file types, protected-prefix precedence, race resistance, and scan bounds.
"""
import errno
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

    def test_identity_fstat_failure_blocks_candidate_and_scan_continues(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        broken = make_prefix(managed, "a-broken")
        good = make_prefix(managed, "z-good")
        real_identity = discovery._identity
        calls = 0

        def fail_first_identity(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.EIO, "injected fstat failure")
            return real_identity(fd)

        with mock.patch.object(discovery, "_identity", side_effect=fail_first_identity):
            prefixes, evidence = discover_prefixes(home=self.home)

        self.assertFalse(any(item["path"] == str(broken) for item in prefixes))
        self.assertTrue(any(item["path"] == str(good) for item in prefixes))
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "identity could not be verified" in item.summary
            for item in evidence
        ))

    def test_identity_fstat_failure_still_closes_candidate_descriptor(self):
        prefix = make_prefix(self.home)
        captured = []

        def fail_identity(fd):
            captured.append(fd)
            raise OSError(errno.EIO, "injected fstat failure")

        with mock.patch.object(discovery, "_identity", side_effect=fail_identity):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNone(info)
        self.assertEqual(len(captured), 1)
        with self.assertRaises(OSError) as raised:
            os.fstat(captured[0])
        self.assertEqual(raised.exception.errno, errno.EBADF)
        self.assertTrue(any(item.status.value == "BLOCKED" for item in evidence))

    def test_marker_stat_oserrors_fail_closed(self):
        prefix = make_prefix(self.home)
        for error_number in (errno.EACCES, errno.EPERM, errno.ESTALE, errno.EIO):
            with self.subTest(errno=error_number):
                real_stat_at = discovery._stat_at

                def fail_user_marker(fd, name, *, _error=error_number):
                    if name == "user.reg":
                        raise OSError(_error, os.strerror(_error))
                    return real_stat_at(fd, name)

                with mock.patch.object(discovery, "_stat_at", side_effect=fail_user_marker):
                    info, evidence = inspect_prefix(prefix, home=self.home)

                self.assertIsNone(info)
                self.assertTrue(any(
                    item.status.value == "BLOCKED"
                    and "marker could not be examined safely" in item.summary
                    for item in evidence
                ))

    def test_nested_probe_descriptor_exhaustion_is_unknown_not_absent(self):
        prefix = make_prefix(self.home)
        for error_number in (errno.EMFILE, errno.ENFILE):
            with self.subTest(errno=error_number):
                with mock.patch.object(
                    discovery.os, "dup",
                    side_effect=OSError(error_number, os.strerror(error_number)),
                ):
                    info, evidence = inspect_prefix(prefix, home=self.home)

                self.assertIsNotNone(info)
                self.assertIsNone(info["syswow64_present"])
                self.assertIsNone(info["arch_conflict"])
                self.assertTrue(any(
                    item.status.value == "BLOCKED"
                    and "Nested Windows architecture path" in item.summary
                    for item in evidence
                ))

    def test_container_scandir_oserror_is_blocked_without_crashing(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "good")
        real_scandir = os.scandir

        def fail_target_root(path):
            if isinstance(path, int):
                raise OSError(errno.EIO, "injected scandir failure")
            return real_scandir(path)

        with mock.patch.object(discovery.os, "scandir", side_effect=fail_target_root):
            prefixes, evidence = discover_prefixes(
                home=self.home,
                container_roots=[managed],
            )

        self.assertFalse(prefixes)
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "Container root could not be listed" in item.summary
            for item in evidence
        ))

    def test_candidate_open_oserror_matrix_is_classified_safely(self):
        prefix = make_prefix(self.home)
        cases = (
            (errno.EACCES, "BLOCKED"),
            (errno.EPERM, "BLOCKED"),
            (errno.ELOOP, "BLOCKED"),
            (errno.ENOTDIR, "UNPROVEN"),
            (errno.ENOENT, "UNPROVEN"),
            (errno.ESTALE, "BLOCKED"),
            (errno.EIO, "BLOCKED"),
            (errno.EMFILE, "BLOCKED"),
            (errno.ENFILE, "BLOCKED"),
        )
        for error_number, expected_status in cases:
            with self.subTest(errno=error_number):
                def fail_candidate(path):
                    if Path(path) == prefix:
                        raise OSError(error_number, os.strerror(error_number))
                    raise AssertionError("unexpected path opened")

                with mock.patch.object(
                    discovery, "_open_dir_path", side_effect=fail_candidate
                ):
                    info, evidence = inspect_prefix(prefix, home=self.home)

                self.assertIsNone(info)
                self.assertTrue(any(
                    item.status.value == expected_status
                    for item in evidence
                ))

    def test_registry_open_oserrors_fail_closed(self):
        prefix = make_prefix(self.home)
        real_open = os.open
        for error_number in (
            errno.EACCES, errno.ELOOP, errno.ESTALE,
            errno.EIO, errno.EMFILE, errno.ENFILE,
        ):
            with self.subTest(errno=error_number):
                def fail_registry_open(path, flags, *args, **kwargs):
                    if path == "system.reg" and kwargs.get("dir_fd") is not None:
                        raise OSError(error_number, os.strerror(error_number))
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(
                    discovery.os, "open", side_effect=fail_registry_open
                ):
                    info, evidence = inspect_prefix(prefix, home=self.home)

                self.assertIsNone(info)
                self.assertTrue(any(
                    item.status.value == "BLOCKED"
                    and "header could not be read safely" in item.summary
                    for item in evidence
                ))

    def test_registry_read_oserror_closes_open_descriptor(self):
        prefix = make_prefix(self.home)
        real_read = os.read
        opened_registry_fds = []

        def track_registry_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if path == "system.reg" and kwargs.get("dir_fd") is not None:
                opened_registry_fds.append(fd)
            return fd

        real_open = os.open

        def fail_registry_read(fd, size):
            if fd in opened_registry_fds:
                raise OSError(errno.EIO, "injected registry read failure")
            return real_read(fd, size)

        with mock.patch.object(discovery.os, "open", side_effect=track_registry_open):
            with mock.patch.object(discovery.os, "read", side_effect=fail_registry_read):
                info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNone(info)
        self.assertEqual(len(opened_registry_fds), 1)
        with self.assertRaises(OSError) as raised:
            os.fstat(opened_registry_fds[0])
        self.assertEqual(raised.exception.errno, errno.EBADF)
        self.assertTrue(any(item.status.value == "BLOCKED" for item in evidence))

    def test_nested_component_open_oserror_is_unknown_not_absent(self):
        prefix = make_prefix(self.home)
        (prefix / "drive_c" / "windows" / "syswow64").mkdir(parents=True)
        real_open = os.open

        for error_number in (errno.EACCES, errno.ELOOP, errno.ESTALE, errno.EIO):
            with self.subTest(errno=error_number):
                def fail_windows_open(path, flags, *args, **kwargs):
                    if path == "windows" and kwargs.get("dir_fd") is not None:
                        raise OSError(error_number, os.strerror(error_number))
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(
                    discovery.os, "open", side_effect=fail_windows_open
                ):
                    info, evidence = inspect_prefix(prefix, home=self.home)

                self.assertIsNotNone(info)
                self.assertIsNone(info["syswow64_present"])
                self.assertIsNone(info["arch_conflict"])
                self.assertTrue(any(
                    item.status.value == "BLOCKED"
                    and "Nested Windows architecture path" in item.summary
                    for item in evidence
                ))

    def test_real_fstat_failure_blocks_one_candidate_and_closes_descriptor(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        broken = make_prefix(managed, "a-broken")
        good = make_prefix(managed, "z-good")
        real_open_dir = discovery._open_dir_path
        real_fstat = os.fstat
        candidate_fds_to_fail = set()
        failed_fd_numbers = []

        def track_candidate_descriptor(path):
            fd = real_open_dir(path)
            if Path(path) == broken:
                candidate_fds_to_fail.add(fd)
            return fd

        def fail_candidate_fstat(fd):
            if fd in candidate_fds_to_fail:
                # File descriptor numbers may be reused after the failed
                # candidate is closed; consume the injection exactly once.
                candidate_fds_to_fail.remove(fd)
                failed_fd_numbers.append(fd)
                raise OSError(errno.EIO, "injected os.fstat failure")
            return real_fstat(fd)

        with mock.patch.object(
            discovery, "_open_dir_path", side_effect=track_candidate_descriptor
        ):
            with mock.patch.object(
                discovery.os, "fstat", side_effect=fail_candidate_fstat
            ):
                prefixes, evidence = discover_prefixes(home=self.home)

        self.assertFalse(any(item["path"] == str(broken) for item in prefixes))
        self.assertTrue(any(item["path"] == str(good) for item in prefixes))
        self.assertEqual(len(failed_fd_numbers), 1)
        for fd in failed_fd_numbers:
            with self.assertRaises(OSError) as raised:
                real_fstat(fd)
            self.assertEqual(raised.exception.errno, errno.EBADF)
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "identity could not be verified" in item.summary
            for item in evidence
        ))

    def test_deep_component_chain_is_traversed_safely(self):
        parent = self.home
        for index in range(48):
            parent = parent / f"component-{index:02d}"
            parent.mkdir()
        prefix = make_prefix(parent, "deep-prefix")

        info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNotNone(info)
        self.assertEqual(info["path"], str(prefix))
        self.assertEqual(info["architecture"], "win64")
        self.assertTrue(any(item.status.value == "PROVEN" for item in evidence))

    def test_permission_denied_intermediate_stat_is_blocked(self):
        restricted = self.home / "restricted"
        restricted.mkdir()
        prefix = make_prefix(restricted, "prefix")
        real_stat = os.stat

        def deny_intermediate_stat(path, *args, **kwargs):
            if path == "restricted" and kwargs.get("dir_fd") is not None:
                raise PermissionError(errno.EACCES, "injected intermediate stat denial")
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(discovery.os, "stat", side_effect=deny_intermediate_stat):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNone(info)
        self.assertTrue(any(item.status.value == "BLOCKED" for item in evidence))

    def test_permission_denied_intermediate_open_is_blocked(self):
        restricted = self.home / "restricted"
        restricted.mkdir()
        prefix = make_prefix(restricted, "prefix")
        real_open = os.open

        def deny_intermediate_open(path, flags, *args, **kwargs):
            if path == "restricted" and kwargs.get("dir_fd") is not None:
                raise PermissionError(errno.EACCES, "injected intermediate open denial")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(discovery.os, "open", side_effect=deny_intermediate_open):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNone(info)
        self.assertTrue(any(item.status.value == "BLOCKED" for item in evidence))

    def test_not_directory_intermediate_component_is_unproven(self):
        broken = self.home / "broken"
        broken.mkdir()
        prefix = make_prefix(broken, "prefix")
        real_stat = os.stat

        def inject_not_directory(path, *args, **kwargs):
            if path == "broken" and kwargs.get("dir_fd") is not None:
                raise NotADirectoryError(errno.ENOTDIR, "injected ENOTDIR")
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(discovery.os, "stat", side_effect=inject_not_directory):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNone(info)
        self.assertTrue(any(item.status.value == "UNPROVEN" for item in evidence))

    def test_nested_permission_denied_is_unknown_not_absent(self):
        prefix = make_prefix(self.home)
        (prefix / "drive_c" / "windows" / "syswow64").mkdir(parents=True)
        real_stat_at = discovery._stat_at

        def deny_windows_component(fd, name):
            if name == "windows":
                raise PermissionError(errno.EACCES, "injected nested permission denial")
            return real_stat_at(fd, name)

        with mock.patch.object(discovery, "_stat_at", side_effect=deny_windows_component):
            info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNotNone(info)
        self.assertIsNone(info["syswow64_present"])
        self.assertIsNone(info["arch_conflict"])
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "Nested Windows architecture path" in item.summary
            for item in evidence
        ))


if __name__ == "__main__":
    unittest.main()
