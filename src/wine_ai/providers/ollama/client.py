import json
import urllib.error
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/tags"
MAX_RESPONSE_SIZE = 1_000_000


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def list_local_models(timeout: float = 3.0) -> dict:
    """Read local Ollama model inventory. Never sends a prompt."""
    opener = urllib.request.build_opener(NoRedirectHandler())
    request = urllib.request.Request(
        OLLAMA_URL,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            if response.geturl() != OLLAMA_URL:
                return {
                    "reachable": False,
                    "error": "Unexpected response URL",
                }
            raw = response.read(MAX_RESPONSE_SIZE + 1)

        if len(raw) > MAX_RESPONSE_SIZE:
            return {
                "reachable": False,
                "error": "Response exceeds size limit",
            }

        payload = json.loads(raw.decode("utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON structure")

        models = payload.get("models", [])
        if not isinstance(models, list):
            raise ValueError("Invalid models field")

        safe_models = []
        for model in models:
            if isinstance(model, dict):
                safe_models.append({
                    "name": model.get("name"),
                    "size": model.get("size"),
                })

        return {
            "reachable": True,
            "models": safe_models,
        }

    except (
        OSError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        return {
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
