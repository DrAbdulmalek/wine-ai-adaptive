"""Tests for the read-only Wine prefix discovery layer.

Every test runs on temporary directories only — real prefixes on the
machine are never touched, matching the AGENT_POLICY rule that tests
never operate outside ``TemporaryDirectory`` fixtures.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.bottle_manager.discovery import (
    MAX_SCAN_SUBDIRS,
    REG_HEADER_BYTES,
    discover_prefixes,
    inspect_prefix,
)

MARKER_FILES = ("system.reg", "user.reg", "userdef.reg")


def make_prefix(
    root: Path,
    name: str = "p1",
    arch: str = "win64",
    with_syswow64: bool = False,
) -> Path:
    path = root / name
    (path / "drive_c").mkdir(parents=True)
    (path / "system.reg").write_text(
        f"WINE REGISTRY Version 2\n#arch={arch}\n", encoding="ascii"
    )
    for reg in MARKER_FILES[1:]:
        (path / reg).write_text("## WINE registry\n", encoding="ascii")
    if with_syswow64:
        (path / "drive_c" / "windows" / "syswow64").mkdir(parents=True)
    return path


class DiscoverPrefixesTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        # Keep the host environment from leaking into the results.
        self.env_patcher = mock.patch.dict(os.environ, clear=False)
        self.env = self.env_patcher.start()
        self.env.pop("WINEPREFIX", None)

    def tearDown(self):
        self.env_patcher.stop()
        self.temp.cleanup()

    def test_valid_win64_prefix_discovered_from_managed_root(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "trados", arch="win64")

        prefixes, evidence = discover_prefixes(home=self.home)

        match = [p for p in prefixes if p["name"] == "trados"]
        self.assertEqual(len(match), 1)
        info = match[0]
        self.assertEqual(info["architecture"], "win64")
        self.assertEqual(info["origin"], "managed_root")
        self.assertFalse(info["protected"])
        self.assertFalse(info["arch_conflict"])
        statuses = {item.status.value for item in evidence}
        self.assertIn("PROVEN", statuses)

    def test_win32_prefix_architecture_parsed(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "legacy", arch="win32")

        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "legacy"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["architecture"], "win32")
        self.assertFalse(match[0]["syswow64_present"])

    def test_missing_drive_c_is_not_a_prefix(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        bare = managed / "not-a-prefix"
        bare.mkdir()
        (bare / "system.reg").write_text(
            "WINE REGISTRY Version 2\n#arch=win64\n", encoding="ascii"
        )

        prefixes, evidence = discover_prefixes(home=self.home)
        self.assertEqual(
            [p for p in prefixes if p["name"] == "not-a-prefix"], []
        )
        summaries = [item.summary for item in evidence]
        self.assertTrue(
            any("missing markers" in s for s in summaries),
            f"expected missing-marker evidence, got: {summaries}",
        )

    def test_container_candidate_swap_after_enumeration_is_rejected(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        original = make_prefix(managed, "raced")
        real_open = os.open
        swapped = {"done": False}

        def swap_before_candidate_open(path, flags, *args, **kwargs):
            if path == "raced" and kwargs.get("dir_fd") is not None and not swapped["done"]:
                swapped["done"] = True
                os.rename(original, managed / "displaced")
                replacement = make_prefix(managed, "raced", arch="win32")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch("wine_ai.bottle_manager.discovery.os.open",
                        side_effect=swap_before_candidate_open):
            prefixes, evidence = discover_prefixes(home=self.home)

        self.assertTrue(swapped["done"])
        self.assertNotIn("raced", {item["name"] for item in prefixes})
        self.assertTrue(any(
            item.status.value == "BLOCKED"
            and "changed identity" in item.summary.lower()
            for item in evidence
        ))

    def test_symlinked_prefix_candidate_is_rejected(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        real = make_prefix(self.home, "real-prefix")
        link = managed / "linked"
        os.symlink(real, link)

        prefixes, evidence = discover_prefixes(home=self.home)
        self.assertEqual(
            [p for p in prefixes if p["name"] == "linked"], []
        )
        blocked = [
            item for item in evidence
            if item.status.value == "BLOCKED"
            and "Symlinked prefix candidate" in item.summary
        ]
        self.assertEqual(len(blocked), 1)

    def test_symlinked_registry_file_fails_closed(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = make_prefix(managed, "shady")
        target = self.home / "outside.reg"
        target.write_text(
            "WINE REGISTRY Version 2\n#arch=win64\n", encoding="ascii"
        )
        (prefix / "user.reg").unlink()
        os.symlink(target, prefix / "user.reg")

        prefixes, evidence = discover_prefixes(home=self.home)
        self.assertEqual(
            [p for p in prefixes if p["name"] == "shady"], []
        )
        blocked = [
            item for item in evidence
            if item.status.value == "BLOCKED"
            and "Symlinked registry files" in item.summary
        ]
        self.assertEqual(len(blocked), 1)
        # The symlink itself was not followed or modified.
        self.assertTrue(os.path.islink(prefix / "user.reg"))

    def test_default_prefix_is_flagged_protected(self):
        make_prefix(self.home, ".wine")

        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == ".wine"]
        self.assertEqual(len(match), 1)
        self.assertTrue(match[0]["protected"])
        self.assertEqual(match[0]["origin"], "default_prefix")

    def test_wineprefix_environment_declaration_is_honoured(self):
        target = make_prefix(self.home, "declared")
        self.env["WINEPREFIX"] = str(target)

        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "declared"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["origin"], "WINEPREFIX")
        self.assertFalse(match[0]["protected"])

    def test_missing_wineprefix_declaration_stays_unproven(self):
        _, evidence = discover_prefixes(home=self.home)
        unproven = [
            item for item in evidence
            if item.key == "prefix_discovery"
            and item.status.value == "UNPROVEN"
            and "WINEPREFIX is not declared" in item.summary
        ]
        self.assertEqual(len(unproven), 1)

    def test_absent_wineprefix_target_does_not_crash(self):
        self.env["WINEPREFIX"] = str(self.home / "absent-prefix")

        prefixes, _ = discover_prefixes(home=self.home)
        self.assertEqual(
            [p for p in prefixes if p["name"] == "absent-prefix"], []
        )

    def test_duplicate_candidate_across_roots_is_deduplicated(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        shared = make_prefix(managed, "shared")

        prefixes, _ = discover_prefixes(
            home=self.home,
            container_roots=[managed],
            candidates=[shared],
        )
        match = [p for p in prefixes if p["name"] == "shared"]
        self.assertEqual(len(match), 1)

    def test_unparsable_arch_header_stays_unproven(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = make_prefix(managed, "odd", arch="win64")
        (prefix / "system.reg").write_text("garbage header\n", encoding="ascii")

        prefixes, evidence = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "odd"]
        self.assertEqual(len(match), 1)  # markers suffice for discovery
        self.assertIsNone(match[0]["architecture"])
        unproven = [
            item for item in evidence
            if item.status.value == "UNPROVEN"
            and "left unknown" in item.summary
        ]
        self.assertTrue(unproven)

    def test_real_wine_registry_header_is_parsed(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = managed / "trados"
        (prefix / "drive_c").mkdir(parents=True)
        (prefix / "system.reg").write_text(
            "WINE REGISTRY Version 2\n#arch=win64\n\n[Software\\\\Wine]\n",
            encoding="ascii",
        )
        for reg in MARKER_FILES[1:]:
            (prefix / reg).write_text("## WINE registry\n", encoding="ascii")

        prefixes, evidence = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "trados"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["architecture"], "win64")
        self.assertTrue(any(
            item.status.value == "PROVEN"
            and "Architecture win64 from system.reg header" in item.summary
            for item in evidence
        ))

    def test_arch_line_beyond_a_64_byte_window_is_parsed(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = make_prefix(managed, "padded")
        padding = "#comment line padding\n" * 12  # 264 bytes > 64
        (prefix / "system.reg").write_text(
            f"WINE REGISTRY Version 2\n{padding}#arch=win64\n",
            encoding="ascii",
        )
        self.assertGreater(
            (prefix / "system.reg").stat().st_size, 64
        )
        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "padded"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["architecture"], "win64")

    def test_header_without_arch_line_stays_unproven(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = make_prefix(managed, "oldstyle")
        (prefix / "system.reg").write_text(
            "WINE REGISTRY Version 2\n", encoding="ascii"
        )

        prefixes, evidence = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "oldstyle"]
        self.assertEqual(len(match), 1)
        self.assertIsNone(match[0]["architecture"])
        self.assertTrue(any(
            item.status.value == "UNPROVEN"
            and "left unknown" in item.summary
            for item in evidence
        ))

    def test_arch_line_with_trailing_text_is_not_accepted(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        prefix = make_prefix(managed, "smuggled")
        (prefix / "system.reg").write_text(
            "WINE REGISTRY Version 2\n#arch=win64 extra\n#arch=win324\n",
            encoding="ascii",
        )

        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "smuggled"]
        self.assertEqual(len(match), 1)
        self.assertIsNone(match[0]["architecture"])

    def test_arch_signal_conflict_is_contradicted_not_guessed(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "confused", arch="win32", with_syswow64=True)

        prefixes, evidence = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "confused"]
        self.assertEqual(len(match), 1)
        self.assertTrue(match[0]["arch_conflict"])
        self.assertEqual(match[0]["architecture"], "win32")
        contradicted = [
            item for item in evidence
            if item.status.value == "CONTRADICTED"
        ]
        self.assertTrue(contradicted)

    def test_scan_cap_is_enforced_and_recorded(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        for index in range(MAX_SCAN_SUBDIRS + 4):
            make_prefix(managed, f"filler-{index:03d}")

        prefixes, evidence = discover_prefixes(home=self.home)
        managed_found = [
            p for p in prefixes if p["origin"] == "managed_root"
        ]
        self.assertEqual(len(managed_found), MAX_SCAN_SUBDIRS)
        capped = [
            item for item in evidence
            if item.status.value == "PARTIALLY_PROVEN"
            and "capped" in item.summary
        ]
        self.assertEqual(len(capped), 1)

    def test_file_flood_does_not_hide_prefixes(self):
        # Regression (F3): the old single 64-entry cap counted regular
        # files, so >64 files in the container root hid every real prefix
        # behind an overflow. Files now count only toward MAX_SCAN_ENTRIES.
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "buried", arch="win64")
        for index in range(100):
            (managed / f"noise-{index:03d}.txt").write_text("", encoding="ascii")

        prefixes, evidence = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == "buried"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["architecture"], "win64")
        overflow = [
            item for item in evidence
            if item.status.value == "PARTIALLY_PROVEN"
        ]
        self.assertEqual(overflow, [])

    def test_container_root_with_foreign_file_type_is_skipped(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "good")
        fifo = managed / "a-fifo"
        os.mkfifo(fifo)  # non-directory entries are ignored, never opened

        prefixes, _ = discover_prefixes(home=self.home)
        good = [p for p in prefixes if p["name"] == "good"]
        self.assertEqual(len(good), 1)
        self.assertEqual(
            [p for p in prefixes if p["name"] == "a-fifo"], []
        )

    def test_nothing_on_disk_is_modified(self):
        managed = self.home / ".local" / "share" / "wine-ai-adaptive" / "prefixes"
        managed.mkdir(parents=True)
        make_prefix(managed, "trados")

        def snapshot():
            result = {}
            for base, dirs, files in os.walk(self.home):
                for name in dirs + files:
                    path = Path(base) / name
                    result[str(path)] = stat.S_IMODE(path.lstat().st_mode)
            return result

        before = snapshot()
        discover_prefixes(home=self.home)
        after = snapshot()
        self.assertEqual(before, after)


    def test_fake_arch_line_without_wine_banner_stays_unproven(self):
        prefix = make_prefix(self.home, "fake-banner")
        (prefix / "system.reg").write_bytes(
            b"NOT A WINE REGISTRY\n#arch=win64\n"
        )
        info, evidence = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertIsNone(info["architecture"])
        self.assertTrue(any(
            item.status.value == "UNPROVEN" and "left unknown" in item.summary
            for item in evidence
        ))

    def test_arch_line_after_first_registry_key_is_not_header_metadata(self):
        prefix = make_prefix(self.home, "late-arch")
        (prefix / "system.reg").write_bytes(
            b"WINE REGISTRY Version 2\n"
            b";; header comment\n"
            b"[Software\\Wine]\n"
            b"#arch=win64\n"
        )
        info, _ = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertIsNone(info["architecture"])

    def test_duplicate_identical_arch_options_stay_unproven(self):
        prefix = make_prefix(self.home, "duplicate-arch")
        (prefix / "system.reg").write_bytes(
            b"WINE REGISTRY Version 2\n#arch=win64\n#arch=win64\n"
        )
        info, _ = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertIsNone(info["architecture"])

    def test_conflicting_arch_options_stay_unproven(self):
        prefix = make_prefix(self.home, "conflicting-arch")
        (prefix / "system.reg").write_bytes(
            b"WINE REGISTRY Version 2\n#arch=win32\n#arch=win64\n"
        )
        info, _ = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertIsNone(info["architecture"])

    def test_malformed_arch_option_in_preamble_stays_unproven(self):
        prefix = make_prefix(self.home, "malformed-arch")
        (prefix / "system.reg").write_bytes(
            b"WINE REGISTRY Version 2\n#arch=win64-extra\n"
        )
        info, _ = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertIsNone(info["architecture"])

    def test_arch_option_after_key_does_not_duplicate_header_arch(self):
        prefix = make_prefix(self.home, "arch-after-key")
        (prefix / "system.reg").write_bytes(
            b"WINE REGISTRY Version 2\n"
            b"#arch=win64\n"
            b"[Software\\Wine]\n"
            b"#arch=win32\n"
        )
        info, _ = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertEqual(info["architecture"], "win64")

    def test_registry_header_accepts_crlf_line_endings(self):
        prefix = make_prefix(self.home, "crlf")
        (prefix / "system.reg").write_bytes(
            b"WINE REGISTRY Version 2\r\n#arch=win64\r\n\r\n"
        )

        info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNotNone(info)
        self.assertEqual(info["architecture"], "win64")
        self.assertTrue(any(
            item.status.value == "PROVEN"
            and "Architecture win64 from system.reg header" in item.summary
            for item in evidence
        ))

    def test_registry_header_parser_handles_short_reads(self):
        prefix = make_prefix(self.home, "short-reads")
        real_read = os.read
        read_sizes = []

        def short_read(fd, size):
            read_sizes.append(size)
            return real_read(fd, min(size, 3))

        with mock.patch("wine_ai.bottle_manager.discovery.os.read",
                        side_effect=short_read):
            info, _ = inspect_prefix(prefix, home=self.home)

        self.assertIsNotNone(info)
        self.assertEqual(info["architecture"], "win64")
        self.assertGreater(len(read_sizes), 1)

    def test_registry_header_at_exact_read_limit_is_accepted_at_eof(self):
        prefix = make_prefix(self.home, "header-at-limit")
        banner = b"WINE REGISTRY Version 2\n"
        header = b"#arch=win64\n"
        padding_size = REG_HEADER_BYTES - len(banner) - len(header)
        padding = b"#" + (b"x" * (padding_size - 2)) + b"\n"
        payload = banner + padding + header
        self.assertEqual(len(payload), REG_HEADER_BYTES)
        (prefix / "system.reg").write_bytes(payload)

        info, _ = inspect_prefix(prefix, home=self.home)

        self.assertIsNotNone(info)
        self.assertEqual(info["architecture"], "win64")

    def test_registry_header_truncated_at_read_limit_is_not_accepted(self):
        prefix = make_prefix(self.home, "truncated-header")
        banner = b"WINE REGISTRY Version 2\n"
        token = b"#arch=win64"
        padding_size = REG_HEADER_BYTES - len(banner) - len(token)
        padding = b"#" + (b"x" * (padding_size - 2)) + b"\n"
        payload = banner + padding + token + b"X\n"
        self.assertEqual(len(payload[:REG_HEADER_BYTES]), REG_HEADER_BYTES)
        self.assertGreater(len(payload), REG_HEADER_BYTES)
        (prefix / "system.reg").write_bytes(payload)

        info, evidence = inspect_prefix(prefix, home=self.home)

        self.assertIsNotNone(info)
        self.assertIsNone(info["architecture"])
        self.assertTrue(any(
            item.status.value == "UNPROVEN"
            and "left unknown" in item.summary
            for item in evidence
        ))


class InspectPrefixTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env_patcher = mock.patch.dict(os.environ, clear=False)
        self.env = self.env_patcher.start()
        self.env.pop("WINEPREFIX", None)

    def tearDown(self):
        self.env_patcher.stop()
        self.temp.cleanup()

    def test_inspect_returns_detailed_info(self):
        prefix = make_prefix(self.home, "solo", arch="win64")

        info, evidence = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertEqual(info["architecture"], "win64")
        self.assertFalse(info["protected"])
        self.assertIn("markers", info)
        self.assertTrue(
            any(item.status.value == "PROVEN" for item in evidence)
        )

    def test_inspect_flags_default_prefix_as_protected(self):
        prefix = make_prefix(self.home, ".wine")
        info, _ = inspect_prefix(prefix, home=self.home)
        self.assertIsNotNone(info)
        self.assertTrue(info["protected"])

    def test_inspect_rejects_symlink_without_following(self):
        real = make_prefix(self.home, "real")
        link = self.home / "link"
        os.symlink(real, link)

        info, evidence = inspect_prefix(link, home=self.home)
        self.assertIsNone(info)
        self.assertTrue(os.path.islink(link))  # untouched
        self.assertTrue(
            any(item.status.value == "BLOCKED" for item in evidence)
        )


class ProtectionPrecedenceTests(unittest.TestCase):
    """P1-D contract: re-discovery never lowers protection status."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env_patcher = mock.patch.dict(os.environ, clear=False)
        self.env = self.env_patcher.start()
        self.env.pop("WINEPREFIX", None)

    def tearDown(self):
        self.env_patcher.stop()
        self.temp.cleanup()

    def test_wineprefix_equal_to_default_prefix_remains_protected(self):
        # The user declares $WINEPREFIX pointing at ~/.wine itself:
        # the first emit records it as an unprotected declaration, and
        # the default-prefix pass must upgrade it — never the reverse.
        default_prefix = make_prefix(self.home, ".wine")
        self.env["WINEPREFIX"] = str(default_prefix)

        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == ".wine"]
        self.assertEqual(len(match), 1)
        self.assertTrue(
            match[0]["protected"],
            "re-discovering the default prefix must not lose protection",
        )

    def test_protection_upgrade_keeps_original_origin(self):
        # Origin records the first discovery source; the upgrade only
        # touches the protection flag.
        default_prefix = make_prefix(self.home, ".wine")
        self.env["WINEPREFIX"] = str(default_prefix)

        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == ".wine"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["origin"], "WINEPREFIX")
        self.assertTrue(match[0]["protected"])

    def test_default_prefix_without_declaration_is_untouched(self):
        make_prefix(self.home, ".wine")
        prefixes, _ = discover_prefixes(home=self.home)
        match = [p for p in prefixes if p["name"] == ".wine"]
        self.assertEqual(len(match), 1)
        self.assertTrue(match[0]["protected"])
        self.assertEqual(match[0]["origin"], "default_prefix")


class EmptyCandidateTests(unittest.TestCase):
    """Discovery regression: an empty bottle directory is not a prefix."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env_patcher = mock.patch.dict(os.environ, clear=False)
        self.env = self.env_patcher.start()
        self.env.pop("WINEPREFIX", None)

    def tearDown(self):
        self.env_patcher.stop()
        self.temp.cleanup()

    def test_fully_empty_candidate_directory_is_not_a_prefix(self):
        empty = self.home / "bottle-lab"
        empty.mkdir()
        prefixes, evidence = discover_prefixes(
            home=self.home, candidates=[empty]
        )
        match = [p for p in prefixes if p["path"] == str(empty)]
        self.assertEqual(match, [])
        # The rejection carries explicit evidence instead of silence.
        self.assertTrue(evidence)

    def test_uninitialized_bottle_keeps_unknown_architecture(self):
        # A bottle directory with some markers missing stays UNPROVEN —
        # never guessed into a valid Wine prefix.
        stub = self.home / "stub-bottle"
        stub.mkdir()
        (stub / "drive_c").mkdir()
        prefixes, _ = discover_prefixes(
            home=self.home, candidates=[stub]
        )
        match = [p for p in prefixes if p["name"] == "stub-bottle"]
        # drive_c alone is not enough; the registry files are missing.
        self.assertEqual(match, [])


if __name__ == "__main__":
    unittest.main()
