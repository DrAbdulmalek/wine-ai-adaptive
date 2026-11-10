import unittest

from wine_ai.gpu_compatibility.probe import probe_vulkan


class GPUProbeTests(unittest.TestCase):

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
