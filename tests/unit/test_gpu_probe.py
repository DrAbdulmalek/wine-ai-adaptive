import unittest
from types import SimpleNamespace
from unittest import mock

from wine_ai.gpu_compatibility.probe import parse_vulkan_summary, probe_vulkan

SAMPLE_SUMMARY = """
Devices:
========
GPU0:
        gpuDeviceUUID   = 86801659-0200-0000-0002
        vendorID = 0x8086
        deviceName = Intel(R) HD Graphics 620

GPU1:
        vendorID = 0x10DE
        deviceName = NVIDIA GeForce RTX 3060

GPU2:
        vendorID = 0x1234
        deviceName = Unknown Virtual GPU
"""


class VulkanSummaryParsingTests(unittest.TestCase):

    def test_parses_device_names(self):
        parsed = parse_vulkan_summary(SAMPLE_SUMMARY)
        self.assertEqual(
            parsed["device_names"],
            [
                "Intel(R) HD Graphics 620",
                "NVIDIA GeForce RTX 3060",
                "Unknown Virtual GPU",
            ],
        )

    def test_maps_vendor_ids(self):
        parsed = parse_vulkan_summary(SAMPLE_SUMMARY)
        self.assertEqual(parsed["vendors"], ["intel", "nvidia", "unknown"])
        self.assertEqual(parsed["primary_vendor"], "intel")

    def test_no_devices_yields_unknown(self):
        parsed = parse_vulkan_summary("no devices here")
        self.assertEqual(parsed["device_names"], [])
        self.assertEqual(parsed["vendors"], [])
        self.assertEqual(parsed["primary_vendor"], "unknown")

    def test_device_list_is_capped(self):
        text = "\n".join(
            "deviceName = GPU %d" % i for i in range(25)
        )
        parsed = parse_vulkan_summary(text)
        self.assertEqual(len(parsed["device_names"]), 10)

    def test_device_name_truncated_to_128(self):
        parsed = parse_vulkan_summary("deviceName = " + "x" * 500)
        self.assertEqual(len(parsed["device_names"][0]), 128)


class GPUProbeTests(unittest.TestCase):

    def test_probe_uses_bare_allowlisted_command(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout="vendorID = 0x8086\ndeviceName = Test GPU\n",
            stderr="",
            error=None,
        )
        with (
            mock.patch("wine_ai.gpu_compatibility.probe.shutil.which",
                       return_value="/usr/bin/vulkaninfo"),
            mock.patch("wine_ai.gpu_compatibility.probe.run_read_only",
                       return_value=completed) as run,
        ):
            data, evidence = probe_vulkan()
        run.assert_called_once_with(["vulkaninfo", "--summary"], timeout=12)
        self.assertTrue(data["available"])
        self.assertEqual(data["device_names"], ["Test GPU"])

    def test_probe_returns_structured_result(self):

        data, evidence = probe_vulkan()

        self.assertIn("available", data)
        self.assertIsInstance(data["available"], bool)

        self.assertGreaterEqual(len(evidence), 1)

        self.assertIn(
            evidence[0].status.value,
            (
                "PROVEN",
                "PARTIALLY_PROVEN",
                "UNPROVEN",
            ),
        )


if __name__ == "__main__":
    unittest.main()
