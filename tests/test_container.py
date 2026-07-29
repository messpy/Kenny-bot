from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import discord

from src.kennybot.bot import MyBot
from src.kennybot.bootstrap import create_bot
from src.kennybot.container import AppContainer, ClientTextRunner, build_app_container


def _fake_container() -> SimpleNamespace:
    return SimpleNamespace(
        spam_guard=object(),
        meeting_minutes=object(),
        ai_progress_tracker=object(),
        ollama_runner=object(),
        chat_memory=object(),
        chat_service=object(),
        ai_search=object(),
        ollama_client=object(),
        ollama_embed_client=object(),
        ollama_model="test-model",
        openai_vision_client=None,
        openai_image_client=None,
        gemini_vision_client=None,
        gemini_image_client=None,
    )


class ContainerTests(TestCase):
    def test_build_app_container_creates_expected_dependencies(self) -> None:
        container = build_app_container()

        self.assertIsInstance(container, AppContainer)
        self.assertIsNotNone(container.spam_guard)
        self.assertIsNotNone(container.meeting_minutes)
        self.assertIsNotNone(container.ai_progress_tracker)
        self.assertIsNotNone(container.chat_memory)
        self.assertIsNotNone(container.chat_service)
        self.assertIsNotNone(container.ollama_client)
        self.assertIsNotNone(container.ollama_embed_client)
        self.assertTrue(container.ollama_model)
        if container.ai_search is not None:
            self.assertIsInstance(container.ai_search.runner, ClientTextRunner)
            self.assertIsInstance(container.ai_search.summarizer.runner, ClientTextRunner)

    def test_client_text_runner_uses_chat_simple(self) -> None:
        import asyncio

        calls = []

        class FakeClient:
            def chat_simple(self, *, model: str, prompt: str) -> str:
                calls.append((model, prompt))
                return "ok"

        runner = ClientTextRunner(FakeClient())  # type: ignore[arg-type]

        result = asyncio.run(runner.run_async("prompt", model="gemini-2.5-flash"))

        self.assertEqual(result, "ok")
        self.assertEqual(calls, [("gemini-2.5-flash", "prompt")])

    def test_mybot_exposes_existing_attributes_from_container(self) -> None:
        container = _fake_container()
        bot = MyBot(
            command_prefix=lambda _bot, _message: [],
            intents=discord.Intents.none(),
            container=container,
        )

        self.assertIs(bot.container, container)
        self.assertIs(bot.spam_guard, container.spam_guard)
        self.assertIs(bot.meeting_minutes, container.meeting_minutes)
        self.assertIs(bot.ai_progress_tracker, container.ai_progress_tracker)
        self.assertIs(bot.chat_memory, container.chat_memory)
        self.assertIs(bot.chat_service, container.chat_service)
        self.assertIs(bot.ai_search, container.ai_search)
        self.assertIs(bot.ollama_client, container.ollama_client)
        self.assertIs(bot.ollama_embed_client, container.ollama_embed_client)
        self.assertEqual(bot.ollama_model, "test-model")

    def test_create_bot_builds_container_before_mybot(self) -> None:
        container = _fake_container()

        with patch("src.kennybot.bootstrap.build_app_container", return_value=container):
            bot = create_bot()

        self.assertIs(bot.container, container)
