"""Tests for the bundled GPU vendor knowledge."""

import unittest

from wine_ai.gpu_compatibility.vendor_data import (
    expected_errors_for,
    is_plausible_graphics_error,
    load_vendor_map,
    normalize_vendor,
    vendor_notes,
)


class VendorDataTests(unittest.TestCase):

    def test_map_loads_and_is_validated(self):
        data = load_vendor_map()
        for vendor in ("intel", "amd", "nvidia", "unknown"):
            self.assertIn(vendor, data)
            self.assertIn(
                "common_graphics_errors", data[vendor]
            )

    def test_normalize_vendor(self):
        self.assertEqual(normalize_vendor("AMD Radeon RX 6700"), "amd")
        self.assertEqual(normalize_vendor("NVIDIA Corporation"), "nvidia")
        self.assertEqual(normalize_vendor("Intel UHD 630"), "intel")
        self.assertEqual(normalize_vendor("llvmpipe"), "unknown")
        self.assertEqual(normalize_vendor(""), "unknown")
        self.assertEqual(normalize_vendor(None), "unknown")

    def test_expected_errors_for_vendor(self):
        nvidia = expected_errors_for("nvidia")
        self.assertIn("0x887A0005", nvidia)
        self.assertEqual(expected_errors_for("unknown"), [])

    def test_plausibility_check(self):
        self.assertTrue(
            is_plausible_graphics_error("nvidia", "0x887A0005")
        )
        self.assertFalse(
            is_plausible_graphics_error("intel", "0x887A0005")
        )
        self.assertFalse(is_plausible_graphics_error("amd", None))

    def test_notes_are_strings(self):
        self.assertIsInstance(vendor_notes("amd"), str)
        self.assertTrue(vendor_notes("amd"))


if __name__ == "__main__":
    unittest.main()
