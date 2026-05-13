import sys
from pathlib import Path
from types import SimpleNamespace
import types
import unittest
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "ollama" not in sys.modules:
    ollama = types.ModuleType("ollama")

    class _Client:
        pass

    class _ResponseError(Exception):
        pass

    ollama.Client = _Client
    ollama.ResponseError = _ResponseError
    sys.modules["ollama"] = ollama

from src.kennybot.ai import client as ai_client


class _RateLimitResponse:
    status_code = 429

    def raise_for_status(self) -> None:
        raise requests.HTTPError(
            "429 Client Error: Too Many Requests for url: https://example.invalid/generateContent",
            response=self,
        )


class _CaptureHTTP:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _RateLimitResponse()


class _FallbackClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"role": "assistant", "content": "fallback ok"}}


class AIClientTests(unittest.TestCase):
    def test_gemini_429_falls_back_to_default_local_model_without_url_key(self) -> None:
        service = ai_client.OllamaClientService.__new__(ai_client.OllamaClientService)
        service.client = _FallbackClient()
        service._local_fallback_client = None
        service._http = _CaptureHTTP()
        service._pull_lock = None
        service._ensured_models = set()
        service.config = SimpleNamespace(host=None)

        config = SimpleNamespace(
            ai_models=lambda: SimpleNamespace(
                chat="gemini-2.5-flash",
                summary="gemini-2.5-flash",
                default="gemini-2.5-flash",
            )
        )

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-secret"}, clear=False), patch(
            "src.kennybot.ai.client.get_app_config",
            return_value=config,
        ):
            response = service._gemini_generate_content(
                model="gemini-2.5-flash",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(response["message"]["content"], "fallback ok")
        self.assertEqual(service.client.calls[0]["model"], "gpt-oss:120b-cloud")
        self.assertNotIn("timeout_sec", service.client.calls[0])
        self.assertNotIn("params", service._http.calls[0])
        self.assertEqual(service._http.calls[0]["headers"], {"x-goog-api-key": "test-secret"})
        self.assertNotIn("test-secret", service._http.calls[0]["url"])

    def test_candidate_models_do_not_invent_cloud_variant(self) -> None:
        service = ai_client.OllamaClientService.__new__(ai_client.OllamaClientService)
        config = SimpleNamespace(
            ai_models=lambda: SimpleNamespace(
                chat="gpt-oss:120b-cloud",
                summary="gpt-oss:120b-cloud",
                default="gpt-oss:120b-cloud",
            )
        )

        with patch("src.kennybot.ai.client.get_app_config", return_value=config):
            candidates = service._candidate_models("gpt-oss:120b-cloud")

        self.assertEqual(candidates, ["gpt-oss:120b-cloud"])

    def test_cloud_model_does_not_fall_back_to_local_non_cloud_model(self) -> None:
        service = ai_client.OllamaClientService.__new__(ai_client.OllamaClientService)
        service._local_fallback_client = _FallbackClient()

        with self.assertRaisesRegex(RuntimeError, "refusing local fallback"):
            service._try_local_chat_fallback(
                model="gpt-oss:120b-cloud",
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
                format=None,
            )

        self.assertEqual(service._local_fallback_client.calls, [])

    def test_default_client_ignores_ollama_host_for_local_runtime(self) -> None:
        created: list[dict] = []

        class _Client:
            def __init__(self, **kwargs):
                created.append(kwargs)

        with patch("src.kennybot.ai.client.Client", _Client), patch.dict(
            "os.environ",
            {"OLLAMA_HOST": "https://ollama.com"},
            clear=False,
        ):
            ai_client.OllamaClientConfig(timeout_sec=30).build_client()

        self.assertEqual(created, [{"host": "http://127.0.0.1:11434", "timeout": 30.0}])


if __name__ == "__main__":
    unittest.main()
