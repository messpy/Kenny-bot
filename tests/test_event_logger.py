import sys
from pathlib import Path
import unittest
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "discord" not in sys.modules:
    class _DiscordModule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    class _DiscordSubmodule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    discord = _DiscordModule("discord")
    discord.__path__ = []
    discord.Client = object
    discord.Guild = object
    discord.TextChannel = object
    discord.Thread = object
    discord.Message = object
    discord.Color = SimpleNamespace(red=lambda: None, orange=lambda: None, green=lambda: None, blurple=lambda: None)
    discord.Embed = lambda *args, **kwargs: SimpleNamespace(add_field=lambda **_kwargs: None, set_footer=lambda **_kwargs: None)
    discord.utils = SimpleNamespace(utcnow=lambda: None)
    discord.AllowedMentions = SimpleNamespace(none=lambda: None)
    utils = _DiscordSubmodule("discord.utils")
    utils.get = lambda *args, **kwargs: None
    sys.modules["discord"] = discord
    sys.modules["discord.utils"] = utils

from src.kennybot.utils import event_logger


class EventLoggerTests(unittest.TestCase):
    def test_resolve_event_log_channel_accepts_thread(self) -> None:
        class FakeThread:
            def __init__(self, channel_id: int):
                self.id = channel_id

        thread = FakeThread(1518267649786908743)
        bot = SimpleNamespace(
            get_channel=Mock(return_value=thread),
            fetch_channel=AsyncMock(return_value=thread),
        )
        guild = SimpleNamespace(id=664237144600215581)

        with patch.object(event_logger._settings, "get", return_value=1518267649786908743):
            with patch.object(event_logger.discord, "Thread", FakeThread), patch.object(
                event_logger.discord, "TextChannel", type("FakeTextChannel", (), {})
            ):
                import asyncio

                resolved = asyncio.run(event_logger.resolve_event_log_channel(bot, guild))

        self.assertIs(resolved, thread)

    def test_send_event_log_posts_to_thread(self) -> None:
        sent_message = SimpleNamespace(id=1)

        class FakeThread:
            def __init__(self, channel_id: int):
                self.id = channel_id
                self.send = AsyncMock(return_value=sent_message)

        thread = FakeThread(1518267649786908743)
        bot = SimpleNamespace(
            get_channel=Mock(return_value=thread),
            fetch_channel=AsyncMock(return_value=thread),
        )
        guild = SimpleNamespace(id=664237144600215581)

        with patch.object(event_logger._settings, "get", return_value=1518267649786908743):
            with patch.object(event_logger.discord, "Thread", FakeThread), patch.object(
                event_logger.discord, "TextChannel", type("FakeTextChannel", (), {})
            ):
                import asyncio

                message = asyncio.run(
                    event_logger.send_event_log(
                        bot,
                        guild=guild,
                        title="Bot 管理ログ",
                        description="thread test",
                        fields=[("項目", "値", False)],
                    )
                )

        self.assertIs(message, sent_message)
        thread.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
