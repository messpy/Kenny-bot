import sys
from pathlib import Path
import unittest
import types
from types import SimpleNamespace


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
    discord.abc = _DiscordSubmodule("discord.abc")
    discord.abc.Messageable = object
    ext = types.ModuleType("discord.ext")
    commands = types.ModuleType("discord.ext.commands")

    class _Cog:
        @classmethod
        def listener(cls, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class _Bot:
        pass

    commands.Cog = _Cog
    commands.Bot = _Bot
    ext.commands = commands
    discord.ext = ext
    utils = _DiscordSubmodule("discord.utils")
    utils.get = lambda *args, **kwargs: None
    discord.utils = utils
    sys.modules["discord"] = discord
    sys.modules["discord.abc"] = discord.abc
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands
    sys.modules["discord.utils"] = utils

from src.kennybot.cogs.message_logger import MessageLogger
from src.kennybot.guards.mod_actions import ModActions


class MessageLoggerSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = MessageLogger.__new__(MessageLogger)

    def test_context_target_candidates_prefers_reply_then_mentions(self) -> None:
        author = SimpleNamespace(id=1, display_name="author", name="author", bot=False)
        replied = SimpleNamespace(id=2, display_name="reply", name="reply", bot=False)
        mention = SimpleNamespace(id=3, display_name="mention", name="mention", bot=False)
        msg = SimpleNamespace(
            author=author,
            reference=SimpleNamespace(resolved=SimpleNamespace(author=replied)),
            mentions=[replied, mention],
        )

        targets = self.logger._context_target_candidates(msg)

        self.assertEqual(targets["author"], (1, "author"))
        self.assertEqual(targets["replied_user"], (2, "reply"))
        self.assertEqual(targets["mentioned_1"], (3, "mention"))

    def test_person_lookup_plan_promotes_mentioned_target(self) -> None:
        plan = [
            {"source": "recent_user_history"},
            {"source": "recent_turns", "limit": 4},
        ]
        targets = {
            "author": (1, "author"),
            "replied_user": (2, "reply"),
            "mentioned_1": (3, "mention"),
        }

        adjusted = self.logger._prioritize_mentioned_person_plan(
            plan=plan,
            text="この人のプロフィールを教えて",
            target_candidates=targets,
            user_lines=12,
        )

        self.assertEqual(
            [item["source"] for item in adjusted[:4]],
            ["member_history", "recent_turns"],
        )
        self.assertEqual(adjusted[0]["target"], "mentioned_1")

    def test_person_lookup_query_detection(self) -> None:
        self.assertTrue(self.logger._is_person_lookup_query("この人のプロフィールを教えて"))
        self.assertFalse(self.logger._is_person_lookup_query("このサーバーはなにするところ？"))

    def test_channel_profile_query_detection(self) -> None:
        self.assertTrue(self.logger._is_channel_profile_query("このサーバーは何のやつ？"))
        self.assertFalse(self.logger._is_channel_profile_query("このBotの機能を教えて"))

    def test_spam_guard_disables_without_kick_or_ban_permissions(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=False, ban_members=False),
        )
        guild = SimpleNamespace(me=bot_member, get_member=lambda _member_id: None)

        self.assertTrue(
            ModActions.should_disable_spam_guard(
                SimpleNamespace(user=SimpleNamespace(id=999)),
                guild,
            )
        )

    def test_spam_guard_stays_enabled_when_kick_or_ban_is_available(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=False),
        )
        guild = SimpleNamespace(me=bot_member, get_member=lambda _member_id: None)

        self.assertFalse(
            ModActions.should_disable_spam_guard(
                SimpleNamespace(user=SimpleNamespace(id=999)),
                guild,
            )
        )

    def test_collect_message_ids_deduplicates_and_preserves_order(self) -> None:
        messages = [
            {"id": 101},
            {"id": "102"},
            {"id": 101},
            {"id": 0},
            {"id": None},
            {"id": 103},
        ]

        ids = self.logger._collect_message_ids(messages)

        self.assertEqual(ids, ["101", "102", "103"])

    def test_sanitize_user_visible_answer_rewrites_reference_detail_label(self) -> None:
        text = "参照概要と参照詳細を確認してください。"

        sanitized = self.logger._sanitize_user_visible_answer(text)

        self.assertIn("参照元の概要", sanitized)
        self.assertIn("参照元の詳細", sanitized)

    def test_sanitize_user_visible_answer_keeps_confirmed_commands_only(self) -> None:
        text = "関連コマンド: /help /totally_fake_command"

        sanitized = self.logger._sanitize_user_visible_answer(text)

        self.assertIn("/help", sanitized)
        self.assertIn("totally_fake_command", sanitized)
        self.assertNotIn("/totally_fake_command", sanitized)


if __name__ == "__main__":
    unittest.main()
