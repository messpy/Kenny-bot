from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.kennybot.ai.client import OllamaClientService


class OllamaClientFallbackTest(TestCase):
    def test_all_gemini_models_keep_explicit_ollama_fallback(self) -> None:
        service = OllamaClientService.__new__(OllamaClientService)
        models = SimpleNamespace(
            chat="gemini-2.5-flash",
            summary="gemini-2.5-flash",
            default="gemini-2.5-flash",
            fallback=("gpt-oss:120b-cloud",),
        )

        with patch(
            "src.kennybot.ai.client.get_app_config",
            return_value=SimpleNamespace(ai_models=lambda: models),
        ):
            candidates = service._candidate_models("gemini-2.5-flash")

        self.assertEqual(candidates, ["gemini-2.5-flash", "gpt-oss:120b-cloud"])

    def test_legacy_non_gemini_models_remain_fallback_when_explicit_empty(self) -> None:
        service = OllamaClientService.__new__(OllamaClientService)
        models = SimpleNamespace(
            chat="gemini-2.5-flash",
            summary="gpt-oss:120b-cloud",
            default="gpt-oss:120b-cloud",
            fallback=(),
        )

        with patch(
            "src.kennybot.ai.client.get_app_config",
            return_value=SimpleNamespace(ai_models=lambda: models),
        ):
            candidates = service._candidate_models("gemini-2.5-flash")

        self.assertEqual(candidates, ["gemini-2.5-flash", "gpt-oss:120b-cloud"])

    def test_configured_fallback_includes_available_local_models(self) -> None:
        service = OllamaClientService.__new__(OllamaClientService)
        service._local_fallback_client = None
        service.config = SimpleNamespace(_is_local_host=lambda: True)
        service.client = SimpleNamespace(
            list=lambda: {
                "models": [
                    {"model": "embeddinggemma:latest"},
                    {"model": "gpt-oss:120b-cloud"},
                    {"model": "qwen3:4b"},
                ]
            }
        )
        models = SimpleNamespace(fallback=("missing:122b",))

        candidates = service._ollama_fallback_models(models=models)

        self.assertEqual(
            candidates,
            ["missing:122b", "gpt-oss:120b-cloud", "qwen3:4b"],
        )

    def test_gemini_rate_limit_tries_local_fallback_before_primary_client(self) -> None:
        service = OllamaClientService.__new__(OllamaClientService)
        local_client = object()
        primary_client = object()
        service._local_fallback_client = local_client
        service.client = primary_client

        clients = service._gemini_rate_limit_fallback_clients()

        self.assertEqual(clients, [local_client, primary_client])

    def test_local_cloud_model_name_is_preserved_when_available(self) -> None:
        service = OllamaClientService.__new__(OllamaClientService)
        service._local_fallback_client = None
        service.config = SimpleNamespace(_is_local_host=lambda: True)
        service.client = SimpleNamespace(
            list=lambda: {"models": [{"model": "gpt-oss:120b-cloud"}]}
        )

        model = service._normalize_local_fallback_model("gpt-oss:120b-cloud")

        self.assertEqual(model, "gpt-oss:120b-cloud")
