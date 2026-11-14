"""Regression tests for environment inventory command resolution."""

import unittest
from types import SimpleNamespace
from unittest import mock

from wine_ai.environment.inventory import collect_environment


class EnvironmentInventoryTests(unittest.TestCase):
    def test_wine_query_uses_bare_allowlisted_command(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout="wine-10.0\n",
            stderr="",
            error=None,
        )
        def which(name):
            return "/usr/bin/wine" if name == "wine" else None

        with (
            mock.patch("wine_ai.environment.inventory.shutil.which",
                       side_effect=which),
            mock.patch("wine_ai.environment.inventory.run_read_only",
                       return_value=completed) as run,
        ):
            data, evidence = collect_environment()

        run.assert_called_once_with(["wine", "--version"])
        self.assertEqual(data["wine_version"], "wine-10.0")
        self.assertEqual(data["executables"]["wine"], "/usr/bin/wine")


if __name__ == "__main__":
    unittest.main()
