import json
import unittest
from unittest.mock import patch

from wine_ai.providers.ollama.client import list_local_models


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return "http://127.0.0.1:11434/api/tags"

    def read(self, size=-1):
        return self.payload[:size]


class OllamaTests(unittest.TestCase):

    @patch("urllib.request.OpenerDirector.open")
    def test_reads_model_inventory(self, mocked):
        payload = json.dumps({
            "models": [{"name": "example:latest", "size": 1000}]
        }).encode()

        mocked.return_value = FakeResponse(payload)
        result = list_local_models()

        self.assertTrue(result["reachable"])
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "example:latest")

    @patch("urllib.request.OpenerDirector.open")
    def test_handles_unavailable_service(self, mocked):
        mocked.side_effect = OSError("Connection refused")

        result = list_local_models()

        self.assertFalse(result["reachable"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
