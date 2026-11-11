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


if __name__ == "__main__":
    unittest.main()
