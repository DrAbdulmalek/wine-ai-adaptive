"""Tests for the read-only environment snapshot collector."""

import tempfile
import unittest
from pathlib import Path

from wine_ai.environment.snapshot import (
    EnvironmentSnapshot,
    collect,
    detect_launcher,
    detect_wine_version,
    distro_name,
    parse_wine_version,
)


class LauncherDetectionTests(unittest.TestCase):

    def test_detects_steam(self):
        env = {"SteamAppId": "1245620"}
        self.assertEqual(detect_launcher(env), "steam")

    def test_detects_lutris(self):
        env = {"LUTRIS_GAME_UUID": "abc"}
        self.assertEqual(detect_launcher(env), "lutris")

    def test_detects_bottles_and_heroic(self):
        self.assertEqual(detect_launcher({"BOTTLES_ENV": "1"}), "bottles")
        self.assertEqual(detect_launcher({"HEROIC_PATH": "x"}), "heroic")

    def test_unknown_launcher(self):
        self.assertEqual(detect_launcher({"PATH": "/usr/bin"}), "unknown")
        self.assertEqual(detect_launcher({}), "unknown")

    def test_rejects_non_mapping(self):
        with self.assertRaises(ValueError):
            detect_launcher(["SteamAppId=1"])


class WineVersionTests(unittest.TestCase):

    def test_parse_wine_version(self):
        self.assertEqual(parse_wine_version("wine-11.19"), "11.19")
        self.assertEqual(parse_wine_version("wine-9.0.1"), "9.0.1")
        self.assertIsNone(parse_wine_version("no version here"))
        self.assertIsNone(parse_wine_version(None))

    def test_detect_wine_version_uses_environment_hint(self):
        self.assertEqual(
            detect_wine_version({"WINE_VERSION": "wine-9.0"}), "9.0"
        )
        self.assertIsNone(detect_wine_version({}))


class DistroNameTests(unittest.TestCase):

    def test_reads_os_release_id(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "os-release"
            fake.write_text(
                'NAME="Manjaro Linux"\nID=manjaro\n'
                'HOME_URL="https://manjaro.org"\n',
                encoding="utf-8",
            )
            self.assertEqual(distro_name(fake), "manjaro")

    def test_missing_file_yields_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(
                distro_name(Path(temp) / "missing"), "unknown"
            )

    def test_only_short_id_is_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "os-release"
            fake.write_text(
                'NAME="Super Secret Distro Build"\nID=leakfree\n',
                encoding="utf-8",
            )
            result = distro_name(fake)
            self.assertEqual(result, "leakfree")
            self.assertNotIn("Secret", result)


class SnapshotTests(unittest.TestCase):

    def test_collect_reduces_prefix_to_base_name(self):
        snapshot = collect(
            env={"SteamAppId": "1", "WINE_VERSION": "wine-9.0"},
            prefix_path="/home/user/private/compatdata/1245620",
        )
        self.assertEqual(snapshot.prefix_name, "1245620")
        self.assertNotIn("/home/user", snapshot.to_dict().__repr__())
        self.assertEqual(snapshot.launcher, "steam")
        self.assertEqual(snapshot.wine_version, "9.0")

    def test_collect_without_prefix(self):
        snapshot = collect(env={})
        self.assertEqual(snapshot.prefix_name, "unknown")
        self.assertIsNone(snapshot.to_prefix_id())

    def test_to_record_fields_are_store_compatible(self):
        snapshot = EnvironmentSnapshot(
            wine_version="9.0",
            prefix_name="game-1",
            launcher="steam",
            distro="manjaro",
        )
        fields = snapshot.to_record_fields()
        self.assertEqual(fields, {"wine_version": "9.0", "distro": "manjaro"})

    def test_snapshot_rejects_path_like_values(self):
        with self.assertRaises(ValueError):
            EnvironmentSnapshot(
                wine_version="/home/user/private",
                prefix_name="ok",
                launcher="steam",
                distro="manjaro",
            )
        with self.assertRaises(ValueError):
            EnvironmentSnapshot(
                wine_version="9.0",
                prefix_name="ok",
                launcher="not-a-launcher",
                distro="manjaro",
            )


class BareWineLauncherTests(unittest.TestCase):

    def test_bare_wine_session_reports_wine(self):
        # WINEPREFIX declared, no launcher markers: explicit "wine".
        env = {"WINEPREFIX": "/home/user/prefix"}
        self.assertEqual(
            detect_launcher(env, prefix_declared=True), "wine"
        )
        snapshot = collect(env=env, prefix_path="/home/user/prefix")
        self.assertEqual(snapshot.launcher, "wine")
        self.assertEqual(snapshot.to_launcher(), "wine")

    def test_no_prefix_declared_stays_unknown(self):
        env = {"PATH": "/usr/bin"}
        self.assertEqual(
            detect_launcher(env, prefix_declared=False), "unknown"
        )
        self.assertIsNone(collect(env=env).to_launcher())

    def test_launcher_marker_wins_over_bare_wine(self):
        env = {"SteamAppId": "1245620", "WINEPREFIX": "/home/u/p"}
        self.assertEqual(
            detect_launcher(env, prefix_declared=True), "steam"
        )


class PrefixHashTests(unittest.TestCase):

    def test_hash_is_truncated_hex_without_path_data(self):
        hash_value = collect(prefix_path="/home/user/My Prefix").prefix_hash
        self.assertIsNotNone(hash_value)
        self.assertEqual(len(hash_value), 16)
        self.assertRegex(hash_value, r"^[a-f0-9]{16}$")
        self.assertNotIn("/", hash_value)
        self.assertNotIn("My Prefix", hash_value)

    def test_hash_is_deterministic_and_path_sensitive(self):
        first = collect(prefix_path="/home/user/a").prefix_hash
        again = collect(prefix_path="/home/user/a").prefix_hash
        other = collect(prefix_path="/home/user/b").prefix_hash
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)

    def test_missing_prefix_yields_no_hash(self):
        snapshot = collect(env={})
        self.assertIsNone(snapshot.prefix_hash)
        self.assertIsNone(snapshot.to_prefix_hash())

    def test_hash_helper_rejects_garbage(self):
        from wine_ai.environment.snapshot import prefix_hash_of
        self.assertIsNone(prefix_hash_of(""))
        self.assertIsNone(prefix_hash_of("   "))
        self.assertIsNone(prefix_hash_of(None))

    def test_record_snapshot_with_hash_is_accepted_by_store(self):
        # End-to-end: snapshot fields flow into the store's allow-lists.
        from wine_ai.knowledge.store import KnowledgeStore
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(Path(tmp) / "s.sqlite3")
            snapshot = collect(
                env={"WINEPREFIX": "/home/u/pfx"},
                prefix_path="/home/u/pfx",
            )
            store.add_record(
                wine_version=snapshot.wine_version,
                distro=snapshot.distro,
                error_category="unknown",
                evidence_status="UNPROVEN",
                confidence="low",
                launcher=snapshot.to_launcher(),
                prefix_hash=snapshot.to_prefix_hash(),
            )
            record = store.list_records()[0]
            self.assertEqual(record["launcher"], "wine")
            self.assertEqual(len(record["prefix_hash"]), 16)


if __name__ == "__main__":
    unittest.main()
