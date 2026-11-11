"""Tests for structured error-code extraction and classification."""

import unittest

from wine_ai.diagnostics.codes import (
    MAX_CODES,
    classify_code,
    classify_hresult,
    classify_ntstatus,
    classify_win32,
    extract_error_codes,
)


class ErrorCodesTests(unittest.TestCase):

    def test_extract_known_hresult(self):
        codes = extract_error_codes("Unhandled exception 0xC0000005")
        self.assertEqual(len(codes), 1)
        self.assertEqual(codes[0]["code"], "0xC0000005")
        self.assertEqual(codes[0]["kind"], "HRESULT")
        self.assertEqual(codes[0]["category"], "memory")
        self.assertEqual(codes[0]["confidence"], "high")

    def test_extract_ntstatus_and_win32(self):
        text = (
            "err:module:import_dll failed (STATUS_DLL_NOT_FOUND); "
            "win32 error ERROR_MOD_NOT_FOUND"
        )
        codes = extract_error_codes(text)
        kinds = {c["kind"] for c in codes}
        self.assertEqual(kinds, {"NTSTATUS", "WIN32"})
        categories = {c["code"]: c["category"] for c in codes}
        self.assertEqual(
            categories["STATUS_DLL_NOT_FOUND"], "missing_module"
        )
        self.assertEqual(
            categories["ERROR_MOD_NOT_FOUND"], "missing_module"
        )

    def test_return_addresses_are_ignored(self):
        # 0x7b43bc3d is a typical Wine code address, not an error code.
        codes = extract_error_codes("at address 0x7b43bc3d")
        self.assertEqual(codes, [])

    def test_error_shape_prefix_reported_low_confidence(self):
        codes = extract_error_codes("status 0xC0000221 in module")
        self.assertEqual(len(codes), 1)
        self.assertEqual(codes[0]["code"], "0xC0000221")
        self.assertIsNone(codes[0]["category"])
        self.assertEqual(codes[0]["confidence"], "low")

    def test_duplicates_are_collapsed(self):
        text = "0xC0000005 0xc0000005 0XC0000005"
        codes = extract_error_codes(text)
        self.assertEqual(len(codes), 1)

    def test_limit_is_enforced(self):
        text = " ".join(
            f"STATUS_CODE_{i:03d}" for i in range(50)
        )
        codes = extract_error_codes(text, limit=3)
        self.assertEqual(len(codes), 3)

    def test_limit_validation(self):
        with self.assertRaises(ValueError):
            extract_error_codes("STATUS_DLL_NOT_FOUND", limit=0)
        with self.assertRaises(ValueError):
            extract_error_codes("x", limit=MAX_CODES + 1)
        with self.assertRaises(ValueError):
            extract_error_codes("x", limit=True)
        with self.assertRaises(ValueError):
            extract_error_codes(123)

    def test_no_raw_text_in_output(self):
        text = "SECRET-PATH /home/user/private LOGDATA STATUS_DLL_NOT_FOUND"
        codes = extract_error_codes(text)
        payload = repr(codes)
        self.assertNotIn("SECRET-PATH", payload)
        self.assertNotIn("/home/user", payload)
        self.assertNotIn("LOGDATA", payload)

    def test_classify_helpers(self):
        self.assertEqual(classify_hresult("0x887A0005"), "graphics")
        self.assertEqual(
            classify_ntstatus("STATUS_ACCESS_VIOLATION"), "memory"
        )
        self.assertEqual(classify_win32("ERROR_OUTOFMEMORY"), "memory")
        self.assertIsNone(classify_hresult("0x7b43bc3d"))
        self.assertIsNone(classify_ntstatus("STATUS_MADE_UP_NAME"))

    def test_classify_code_dispatcher(self):
        self.assertEqual(
            classify_code("0xC0000135"), ("HRESULT", "missing_module")
        )
        self.assertEqual(
            classify_code("STATUS_DLL_NOT_FOUND"),
            ("NTSTATUS", "missing_module"),
        )
        self.assertEqual(
            classify_code("ERROR_PROC_NOT_FOUND"),
            ("WIN32", "missing_module"),
        )
        self.assertEqual(classify_code("whatever"), (None, None))
        self.assertEqual(classify_code(42), (None, None))


if __name__ == "__main__":
    unittest.main()
