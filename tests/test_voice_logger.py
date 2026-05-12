import sys
from pathlib import Path
import unittest
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock


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
    discord.Member = object
    discord.VoiceChannel = object
    discord.VoiceState = object
    discord.Guild = object
    discord.__path__ = []
    ext = types.ModuleType("discord.ext")
    ext.__path__ = []
    commands = types.ModuleType("discord.ext.commands")
    utils = _DiscordSubmodule("discord.utils")
    utils.get = lambda *args, **kwargs: None

    class _Cog:
        @classmethod
        def listener(cls, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    commands.Cog = _Cog
    commands.Bot = object
    ext.commands = commands
    discord.ext = ext
    discord.utils = utils
    sys.modules["discord"] = discord
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands
    sys.modules["discord.utils"] = utils

discord_module = sys.modules.get("discord")
if discord_module is not None:
    discord_module.__path__ = []
ext_module = sys.modules.get("discord.ext")
if ext_module is not None:
    ext_module.__path__ = []
else:
    ext_module = types.ModuleType("discord.ext")
    ext_module.__path__ = []
    sys.modules["discord.ext"] = ext_module
commands_module = sys.modules.get("discord.ext.commands")
if commands_module is None:
    commands_module = types.ModuleType("discord.ext.commands")

    class _FallbackCog:
        @classmethod
        def listener(cls, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    commands_module.Cog = _FallbackCog
    commands_module.Bot = object
    sys.modules["discord.ext.commands"] = commands_module
ext_module.commands = commands_module
if discord_module is not None:
    discord_module.ext = ext_module
utils_module = sys.modules.get("discord.utils")
if utils_module is None:
    utils_module = types.ModuleType("discord.utils")
    utils_module.get = lambda *args, **kwargs: None
    sys.modules["discord.utils"] = utils_module
if discord_module is not None:
    discord_module.utils = utils_module

from src.kennybot.cogs.voice_logger import VoiceLogger


class VoiceLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = VoiceLogger(SimpleNamespace())
        self.logger._should_log_channel = lambda guild, channel: True
        self.logger._calculate_duration = lambda join_time: "0:00:05"

    def test_duplicate_leave_is_suppressed(self) -> None:
        member = SimpleNamespace(id=1, mention="<@1>", name="member")
        channel = SimpleNamespace(id=10, name="VC")
        guild = SimpleNamespace(id=100, name="Guild")
        self.logger._voice_join_times[(member.id, guild.id)] = object()

        send_event_log_mock = AsyncMock()
        module = sys.modules["src.kennybot.cogs.voice_logger"]
        original = module.send_event_log
        module.send_event_log = send_event_log_mock
        try:
            import asyncio

            asyncio.run(self.logger._handle_voice_leave(member, channel, guild))
            asyncio.run(self.logger._handle_voice_leave(member, channel, guild))
        finally:
            module.send_event_log = original

        send_event_log_mock.assert_awaited_once()

    def test_voice_join_sends_to_discord_voice_log(self) -> None:
        member = SimpleNamespace(id=1, mention="<@1>", name="member")
        channel = SimpleNamespace(id=10, name="VC")
        guild = SimpleNamespace(id=100, name="Guild")

        send_event_log_mock = AsyncMock()
        module = sys.modules["src.kennybot.cogs.voice_logger"]
        original = module.send_event_log
        module.send_event_log = send_event_log_mock
        try:
            import asyncio

            asyncio.run(self.logger._handle_voice_join(member, channel, guild))
        finally:
            module.send_event_log = original

        self.assertEqual(send_event_log_mock.await_args.kwargs["channel_kind"], "voice")
        self.assertEqual(send_event_log_mock.await_args.kwargs["send_discord"], True)

    def test_voice_leave_sends_to_discord_voice_log_with_duration(self) -> None:
        member = SimpleNamespace(id=1, mention="<@1>", name="member")
        channel = SimpleNamespace(id=10, name="VC")
        guild = SimpleNamespace(id=100, name="Guild")
        self.logger._voice_join_times[(member.id, guild.id)] = object()

        send_event_log_mock = AsyncMock()
        module = sys.modules["src.kennybot.cogs.voice_logger"]
        original = module.send_event_log
        module.send_event_log = send_event_log_mock
        try:
            import asyncio

            asyncio.run(self.logger._handle_voice_leave(member, channel, guild))
        finally:
            module.send_event_log = original

        kwargs = send_event_log_mock.await_args.kwargs
        self.assertEqual(kwargs["channel_kind"], "voice")
        self.assertEqual(kwargs["send_discord"], True)
        self.assertIn(("通話時間", "0:00:05", False), kwargs["fields"])


if __name__ == "__main__":
    unittest.main()
