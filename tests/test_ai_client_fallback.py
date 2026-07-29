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
