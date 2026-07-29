import sys
import types
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "discord" not in sys.modules:
    class _DiscordModule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    discord = _DiscordModule("discord")
    discord.__path__ = []
    discord.Message = object

    class _AllowedMentions:
        @staticmethod
        def none():
            return None

    class _File:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    discord.AllowedMentions = _AllowedMentions
    discord.File = _File
    discord.abc = types.ModuleType("discord.abc")
    discord.abc.__path__ = []
    discord.abc.Messageable = object
    discord.utils = types.ModuleType("discord.utils")
    discord.utils.get = lambda *args, **kwargs: None
    ext = types.ModuleType("discord.ext")
    ext.__path__ = []
    commands = types.ModuleType("discord.ext.commands")

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
    sys.modules["discord"] = discord
    sys.modules["discord.abc"] = discord.abc
    sys.modules["discord.utils"] = discord.utils
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands

from src.kennybot.cogs.message_logger import (
    _extract_image_generation_prompt,
    _looks_like_image_generation_request,
)


class ImageGenerationRequestTests(unittest.TestCase):
    def test_detects_image_generation_request(self) -> None:
        self.assertTrue(_looks_like_image_generation_request("<@123> 画像生成して 夜の東京タワー"))
        self.assertTrue(_looks_like_image_generation_request("猫の絵を描いて"))
        self.assertTrue(_looks_like_image_generation_request("猫を生成して"))
        self.assertTrue(_looks_like_image_generation_request("猫を描いて"))
        self.assertTrue(_looks_like_image_generation_request("猫の画像お願い"))
        self.assertFalse(_looks_like_image_generation_request("この画像を説明して"))
        self.assertFalse(_looks_like_image_generation_request("画像を解析して"))

    def test_extracts_prompt_without_mention_and_command_words(self) -> None:
        self.assertEqual(
            _extract_image_generation_prompt("<@123> 画像生成して 夜の東京タワー", bot_user_id=123),
            "夜の東京タワー",
        )
        self.assertEqual(
            _extract_image_generation_prompt("猫の絵を描いて", bot_user_id=123),
            "猫",
        )
        self.assertEqual(
            _extract_image_generation_prompt("猫を生成して", bot_user_id=123),
            "猫",
        )


if __name__ == "__main__":
    unittest.main()
