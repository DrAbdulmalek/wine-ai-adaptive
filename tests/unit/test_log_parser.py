import tempfile
import unittest

from pathlib import Path

from wine_ai.diagnostics.log_parser import inspect_log


class LogParserTests(unittest.TestCase):

    def test_detects_missing_module(self):

        with tempfile.TemporaryDirectory() as tmp:

            path = Path(tmp) / "wine.log"

            path.write_text(
                "err:module:import_dll missing DLL"
            )

            data, evidence = inspect_log(str(path))

            self.assertEqual(
                data["matches"]["missing_module"],
                1,
            )

            self.assertEqual(
                evidence[0].status.value,
                "PARTIALLY_PROVEN",
            )

    def test_rejects_oversized_log(self):

        with tempfile.TemporaryDirectory() as tmp:

            path = Path(tmp) / "large.log"
            path.write_text("123456789")

            # حد صغير للاختبار فقط
            from wine_ai.diagnostics import log_parser

            original = log_parser.MAX_LOG_SIZE

            try:
                log_parser.MAX_LOG_SIZE = 4

                data, _ = inspect_log(str(path))

                self.assertIn("read_error", data)

            finally:
                log_parser.MAX_LOG_SIZE = original


if __name__ == "__main__":
    unittest.main()
