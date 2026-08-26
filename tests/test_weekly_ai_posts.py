import sys
import types
from datetime import datetime
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
    discord.abc = types.ModuleType("discord.abc")
    discord.abc.__path__ = []
    discord.abc.Messageable = object

    class _AllowedMentions:
        @staticmethod
        def none():
            return None

    discord.AllowedMentions = _AllowedMentions
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
    utils = types.ModuleType("discord.utils")
    utils.get = lambda *args, **kwargs: None
    discord.utils = utils
    sys.modules["discord"] = discord
    sys.modules["discord.abc"] = discord.abc
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands
    sys.modules["discord.utils"] = utils

from src.kennybot.cogs.weekly_ai_posts import (
    WeeklyAiPosts,
    _due_marker,
    _is_daily_schedule,
    _parse_hhmm,
    _parse_weekday,
    _reaction_emojis,
    _safe_post_text,
)


class WeeklyAiPostsTests(unittest.TestCase):
    def test_parse_weekday_accepts_japanese_and_english(self) -> None:
        self.assertEqual(_parse_weekday("月曜"), 0)
        self.assertEqual(_parse_weekday("sun"), 6)
        self.assertIsNone(_parse_weekday("noday"))

    def test_due_marker_only_after_scheduled_time_on_weekday(self) -> None:
        before = datetime(2026, 7, 26, 8, 59)
        due = datetime(2026, 7, 26, 9, 0)
        after = datetime(2026, 7, 26, 9, 1)
        self.assertIsNone(_due_marker(before, weekday=6, hour=9, minute=0))
        self.assertEqual(_due_marker(due, weekday=6, hour=9, minute=0), "2026-07-26")
        self.assertEqual(_due_marker(after, weekday=6, hour=9, minute=0), "2026-07-26")
        self.assertIsNone(_due_marker(after, weekday=0, hour=9, minute=0))

    def test_daily_schedule_is_due_on_any_weekday(self) -> None:
        self.assertTrue(_is_daily_schedule("毎日"))
        self.assertTrue(_is_daily_schedule("daily"))
        monday = datetime(2026, 7, 27, 12, 0)
        sunday = datetime(2026, 7, 26, 12, 0)
        self.assertEqual(_due_marker(monday, weekday=None, hour=12, minute=0), "2026-07-27")
        self.assertEqual(_due_marker(sunday, weekday=None, hour=12, minute=0), "2026-07-26")

    def test_parse_hhmm_and_safe_text(self) -> None:
        self.assertEqual(_parse_hhmm("09:30"), (9, 30))
        self.assertIsNone(_parse_hhmm("24:00"))
        self.assertEqual(_safe_post_text("@everyone hi", max_chars=20), "@\u200beveryone hi")
        self.assertEqual(_safe_post_text("abcdef", max_chars=5), "ab...")

    def test_build_prompt_can_include_today_language_section(self) -> None:
        logger = WeeklyAiPosts.__new__(WeeklyAiPosts)
        prompt = logger._build_prompt({"prompt": "海外豆知識を出す", "today_language": {"enabled": True}})
        self.assertIn("【今日の言語】~〇〇語~", prompt)
        self.assertIn("海外豆知識で扱った国・地域・文化圏に関連する言語", prompt)
        self.assertIn("言語の表記（カタカナ読み）: 意味（日本語）", prompt)
        self.assertIn("「【今日の言語】」の上に空行を1つ入れる", prompt)
        self.assertIn("知らなかったよって人は", prompt)
        self.assertIn("知ってた単語や豆知識があれば", prompt)
        self.assertIn("問題や間違いがありそうなら", prompt)

    def test_build_prompt_includes_recent_posts_to_avoid_duplicates(self) -> None:
        logger = WeeklyAiPosts.__new__(WeeklyAiPosts)
        prompt = logger._build_prompt(
            {"prompt": "海外豆知識を出す"},
            recent_posts=["アイスランドの温泉文化", "メキシコの死者の日"],
        )
        self.assertIn("重複回避", prompt)
        self.assertIn("中心題材が同じネタは避けてください", prompt)
        self.assertIn("アイスランドの温泉文化", prompt)
        self.assertIn("メキシコの死者の日", prompt)

    def test_remember_sent_post_preserves_state_and_caps_history(self) -> None:
        logger = WeeklyAiPosts.__new__(WeeklyAiPosts)
        logger._state = {
            "daily-trivia": {
                "last_sent_date": "2026-07-25",
                "recent_posts": [{"date": "2026-07-25", "sent_at": "old", "summary": "古い豆知識"}],
            }
        }
        logger._remember_sent_post(
            "daily-trivia",
            marker="2026-07-26",
            sent_at="2026-07-26T09:00:00+09:00",
            text="新しい豆知識\n詳しい説明",
            limit=1,
        )
        state_item = logger._state["daily-trivia"]
        self.assertEqual(state_item["last_sent_date"], "2026-07-25")
        self.assertEqual(len(state_item["recent_posts"]), 1)
        self.assertEqual(state_item["recent_posts"][0]["summary"], "新しい豆知識 / 詳しい説明")

    def test_reaction_emojis_are_configurable(self) -> None:
        self.assertEqual(
            _reaction_emojis(
                {
                    "today_language": {
                        "reactions": {
                            "enabled": True,
                            "unknown_emoji": "✋",
                            "known_emoji": "👀",
                            "learned_emoji": "✅",
                            "issue_emoji": "⚠️",
                        }
                    }
                }
            ),
            ["✋", "👀", "✅", "⚠️"],
        )

    def test_reaction_emojis_can_use_global_defaults(self) -> None:
        self.assertEqual(
            _reaction_emojis({"today_language": {"reactions": {"enabled": True}}}),
            ["✋", "👀", "✅", "⚠️"],
        )

    def test_content_key_ignores_channel_and_id(self) -> None:
        logger = WeeklyAiPosts.__new__(WeeklyAiPosts)
        first = {
            "id": "one",
            "channel_id": 1,
            "weekday": "日曜",
            "time": "12:00",
            "timezone": "Asia/Tokyo",
            "model": "gemini-2.5-flash",
            "prompt": "海外豆知識",
            "today_language": {"enabled": True},
        }
        second = dict(first, id="two", channel_id=2)
        self.assertEqual(logger._content_key(first), logger._content_key(second))


if __name__ == "__main__":
    unittest.main()
