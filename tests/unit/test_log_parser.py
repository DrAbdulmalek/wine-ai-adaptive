import os
import tempfile
import unittest

from pathlib import Path

from wine_ai.diagnostics.log_parser import inspect_log, inspect_log_with_content


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

    def test_rejects_symlink_without_reading_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.log"
            link = Path(tmp) / "link.log"
            target.write_text("err:module:import_dll")
            link.symlink_to(target)
            data, evidence = inspect_log(str(link))
            self.assertIn("read_error", data)
            self.assertEqual(evidence[0].status.value, "BLOCKED")

    def test_descriptor_reader_returns_same_content_used_for_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wine.log"
            content = "err:module:import_dll Library missing.dll not found"
            path.write_text(content, encoding="utf-8")
            data, evidence, returned = inspect_log_with_content(str(path))
            self.assertEqual(returned, content)
            self.assertEqual(data["size_bytes"], len(content.encode("utf-8")))
            self.assertEqual(data["matches"]["missing_module"], 1)

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
