from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.kennybot.cogs.birthday_reminders import BirthdayReminders, _normalize_quote_text, _parse_birthday, _should_process_now
from src.kennybot.features.birthday import BirthdayReminderStore


class BirthdayReminderStoreTest(unittest.TestCase):
    def test_upsert_list_due_and_mark_notified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BirthdayReminderStore(Path(tmp) / "birthday.sqlite3")
            record = store.upsert_reminder(
                guild_id=1,
                channel_id=10,
                display_name="雪見もち",
                birthday_date=date(2000, 6, 24),
                created_by_id=99,
                user_id=123,
            )

            self.assertEqual(record.guild_id, 1)
            self.assertEqual(record.channel_id, 10)
            self.assertEqual(record.display_name, "雪見もち")
            self.assertEqual(record.birthday_date, date(2000, 6, 24))

            due = store.list_due_for_today(date(2026, 6, 24))
            self.assertEqual([item.id for item in due], [record.id])

            store.mark_notified(reminder_id=record.id, year=2026)
            self.assertEqual(store.list_due_for_today(date(2026, 6, 24)), [])

            listed = store.list_for_guild(1)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].last_notified_year, 2026)


    def test_fallback_hour_is_enabled(self) -> None:
        self.assertTrue(_should_process_now(12))
        self.assertTrue(_should_process_now(13))
        self.assertFalse(_should_process_now(11))
        self.assertFalse(_should_process_now(14))

    def test_remove_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BirthdayReminderStore(Path(tmp) / "birthday.sqlite3")
            record = store.upsert_reminder(
                guild_id=1,
                channel_id=10,
                display_name="雪見もち",
                birthday_date=date(2000, 6, 24),
                created_by_id=99,
            )
            self.assertTrue(store.remove(guild_id=1, reminder_id=record.id))
            self.assertEqual(store.list_for_guild(1), [])

    def test_quote_text_is_sanitized_and_truncated(self) -> None:
        self.assertEqual(
            _normalize_quote_text("  今日は @everyone いい日だね\n次の行です  "),
            "今日は @\u200beveryone いい日だね 次の行です",
        )
        self.assertEqual(
            _normalize_quote_text("x" * 100, max_chars=12),
            "xxxxxxxxxxxx...",
        )

    def test_parse_birthday_accepts_month_day(self) -> None:
        self.assertEqual(_parse_birthday("06-24"), date(2000, 6, 24))
        self.assertEqual(_parse_birthday("2026-06-24"), date(2026, 6, 24))

    def test_latest_public_quote_uses_most_recent_channel_history(self) -> None:
        class FakeAuthor:
            def __init__(self, bot: bool = False) -> None:
                self.id = 123
                self.bot = bot

        class FakeMessage:
            def __init__(self, content: str, created_at: str, *, bot: bool = False) -> None:
                self.content = content
                self.created_at = created_at
                self.author = FakeAuthor(bot=bot)

        class FakeChannel:
            def __init__(self, messages: list[FakeMessage]) -> None:
                self._messages = messages

            async def history(self, limit: int = 20, oldest_first: bool = False):
                for message in self._messages[:limit]:
                    yield message

        class FakeGuild:
            def __init__(self, channels: list[FakeChannel]) -> None:
                self.id = 1
                self.text_channels = channels
                self.threads = []
                self.default_role = SimpleNamespace()

        class FakeBot:
            def __init__(self) -> None:
                self.user = SimpleNamespace(id=9999)

        from datetime import datetime, timezone

        newer = FakeMessage("最新の公開トーク", datetime(2026, 6, 24, 11, 59, tzinfo=timezone.utc))
        older = FakeMessage("古い公開トーク", datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc))
        channel_a = FakeChannel([older])
        channel_b = FakeChannel([newer])
        cog = BirthdayReminders.__new__(BirthdayReminders)
        cog.bot = FakeBot()
        guild = FakeGuild([channel_a, channel_b])

        async def run() -> str:
            with patch("src.kennybot.cogs.birthday_reminders._is_public_talk_channel", return_value=True):
                return await cog._build_latest_public_quote(guild)  # type: ignore[arg-type]

        quote = asyncio.run(run())
        self.assertEqual(quote, "最新の公開トーク")

    def test_latest_public_quote_skips_url_only_message(self) -> None:
        class FakeAuthor:
            def __init__(self, bot: bool = False) -> None:
                self.id = 123
                self.bot = bot

        class FakeMessage:
            def __init__(self, content: str, created_at: str, *, bot: bool = False) -> None:
                self.content = content
                self.created_at = created_at
                self.author = FakeAuthor(bot=bot)

        class FakeChannel:
            def __init__(self, messages: list[FakeMessage]) -> None:
                self._messages = messages

            async def history(self, limit: int = 20, oldest_first: bool = False):
                for message in self._messages[:limit]:
                    yield message

        class FakeGuild:
            def __init__(self, channels: list[FakeChannel]) -> None:
                self.id = 1
                self.text_channels = channels
                self.threads = []
                self.default_role = SimpleNamespace()

        class FakeBot:
            def __init__(self) -> None:
                self.user = SimpleNamespace(id=9999)

        from datetime import datetime, timezone

        url_only = FakeMessage("https://login.live.com/oauth20_remoteconnect.srf", datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc))
        real_text = FakeMessage("ちゃんとした発言です", datetime(2026, 6, 24, 11, 59, tzinfo=timezone.utc))
        channel = FakeChannel([url_only, real_text])
        cog = BirthdayReminders.__new__(BirthdayReminders)
        cog.bot = FakeBot()
        guild = FakeGuild([channel])

        async def run() -> str:
            with patch("src.kennybot.cogs.birthday_reminders._is_public_talk_channel", return_value=True):
                return await cog._build_latest_public_quote(guild)  # type: ignore[arg-type]

        quote = asyncio.run(run())
        self.assertEqual(quote, "ちゃんとした発言です")


if __name__ == "__main__":
    unittest.main()
