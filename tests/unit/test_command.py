"""Tests for the read-only command allow-list.

``run_read_only`` must refuse to execute anything outside the declared
read-only diagnostic commands, regardless of caller.
"""

import unittest
import stat
from types import SimpleNamespace
from unittest import mock

from wine_ai.command import CommandResult, run_read_only


class RunReadOnlyAllowListTests(unittest.TestCase):

    def test_non_allowlisted_program_is_rejected(self):
        result = run_read_only(["rm", "-rf", "/tmp/should-never-run"])
        self.assertIsNone(result.returncode)
        self.assertEqual(
            result.error, "command not in read-only allow-list"
        )
        self.assertEqual(result.stdout, "")

    def test_shell_invocation_is_rejected(self):
        result = run_read_only(["sh", "-c", "echo hello"])
        self.assertIsNone(result.returncode)
        self.assertEqual(
            result.error, "command not in read-only allow-list"
        )

    def test_allowlisted_program_with_unknown_argument_is_rejected(self):
        result = run_read_only(["wine", "--uninstall"])
        self.assertIsNone(result.returncode)
        self.assertEqual(
            result.error, "command not in read-only allow-list"
        )

    def test_allowlisted_program_accepts_only_known_arguments(self):
        def fake_stat(path):
            if str(path) == "/trusted/bin/vulkaninfo":
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            mock.patch("wine_ai.command.shutil.which", return_value="/trusted/bin/vulkaninfo"),
            mock.patch("wine_ai.command.Path.stat", new=fake_stat),
            mock.patch("wine_ai.command.os.access", return_value=True),
            mock.patch("wine_ai.command.subprocess.run", return_value=completed) as run,
        ):
            result = run_read_only(["vulkaninfo", "--summary"])
        self.assertIsNone(result.error)
        self.assertEqual(result.argv, ("/trusted/bin/vulkaninfo", "--summary"))
        run.assert_called_once()

    def test_custom_executable_path_is_rejected(self):
        result = run_read_only(["/opt/custom/bin/wine", "--version"])
        self.assertEqual(result.error, "command not in read-only allow-list")

    def test_missing_required_argument_is_rejected(self):
        result = run_read_only(["wine"])
        self.assertEqual(result.error, "command not in read-only allow-list")

    def test_extra_argument_is_rejected(self):
        result = run_read_only(["wine", "--version", "--help"])
        self.assertEqual(result.error, "command not in read-only allow-list")

    def test_empty_and_nul_arguments_rejected(self):
        result = run_read_only([])
        self.assertEqual(result.error, "invalid arguments")

        result = run_read_only(["wine", "--ver\x00sion"])
        self.assertEqual(result.error, "invalid arguments")

    def test_result_shape_is_stable(self):
        result = run_read_only(["definitely-not-a-real-program"])
        self.assertIsInstance(result, CommandResult)
        self.assertEqual(
            result.argv, ("definitely-not-a-real-program",)
        )
        self.assertIsNone(result.returncode)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
