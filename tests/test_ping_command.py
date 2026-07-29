from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch
import unittest

from src.kennybot.cogs import slash_commands as slash_commands_module
from src.kennybot.cogs.slash_commands import SlashCommands, VcPanelState


class PingCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_build_ping_text_uses_bot_latency(self) -> None:
        bot = SimpleNamespace(latency=0.1234)
        cog = SlashCommands(bot)

        self.assertEqual(cog._build_ping_text(), "Pong! 123.4ms")

    def test_build_help_text_includes_ping(self) -> None:
        bot = SimpleNamespace(latency=0.1234)
        cog = SlashCommands(bot)

        help_text = cog._build_help_text()

        self.assertIn("/ping: Bot の応答速度を確認", help_text)

    async def test_slash_ping_sends_message(self) -> None:
        bot = SimpleNamespace(latency=0.3456)
        cog = SlashCommands(bot)
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(response=response)

        await SlashCommands.slash_ping.callback(cog, interaction)

        response.send_message.assert_awaited_once_with("Pong! 345.6ms", ephemeral=True)

    async def test_slash_bot_info_sends_embed(self) -> None:
        bot = SimpleNamespace(latency=0.1111, guilds=[])
        cog = SlashCommands(bot)
        response = SimpleNamespace(defer=AsyncMock())
        followup = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(response=response, followup=followup)

        await SlashCommands.slash_bot_info.callback(cog, interaction)

        response.defer.assert_awaited_once_with(ephemeral=True, thinking=False)
        followup.send.assert_awaited_once()
        embed = followup.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Kenny Bot 情報")

    async def test_modpanel_creates_panel(self) -> None:
        class FakeTextChannel:
            def __init__(self) -> None:
                self.send = AsyncMock(return_value=SimpleNamespace(add_reaction=AsyncMock()))

        channel = FakeTextChannel()
        bot = SimpleNamespace(latency=0.1, guilds=[], get_channel=Mock(return_value=channel))
        cog = SlashCommands(bot)
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            response=response,
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=True)),
        )

        with patch.object(slash_commands_module.discord, "TextChannel", FakeTextChannel):
            await SlashCommands.modpanel.callback(cog, interaction)

        channel.send.assert_awaited_once()
        channel.send.return_value.add_reaction.assert_any_await("🔄")
        channel.send.return_value.add_reaction.assert_any_await("📋")
        response.send_message.assert_awaited_once_with("✅ モデレーションパネルを作成しました。", ephemeral=True)

    def test_format_message_export_line_includes_datetime_author_and_body(self) -> None:
        bot = SimpleNamespace(latency=0.1)
        cog = SlashCommands(bot)
        message = SimpleNamespace(
            created_at=datetime(2026, 7, 29, 1, 2, 3, tzinfo=timezone.utc),
            author=SimpleNamespace(id=123, display_name="admin", name="admin"),
            content="hello\nworld",
            attachments=[SimpleNamespace(url="https://example.com/a.png")],
        )

        line = cog._format_message_export_line(message)

        self.assertIn("2026-07-29 10:02:03 JST", line)
        self.assertIn("admin (123)", line)
        self.assertIn("hello\n    world", line)
        self.assertIn("[attachment] https://example.com/a.png", line)

    async def test_export_channel_messages_requires_admin(self) -> None:
        bot = SimpleNamespace(latency=0.1)
        cog = SlashCommands(bot)
        response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        interaction = SimpleNamespace(
            response=response,
            user=SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=False)),
            channel=object(),
        )

        await SlashCommands.export_channel_messages.callback(cog, interaction)

        response.send_message.assert_awaited_once_with("この操作は管理者のみ実行できます。", ephemeral=True)
        response.defer.assert_not_awaited()

    async def test_set_recent_window_requires_admin(self) -> None:
        bot = SimpleNamespace(latency=0.1)
        cog = SlashCommands(bot)
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            response=response,
            user=SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=False)),
            guild=SimpleNamespace(id=1),
        )

        await SlashCommands.set_recent_window.callback(cog, interaction, 50)

        response.send_message.assert_awaited_once_with("この操作は管理者のみ実行できます。", ephemeral=True)

    async def test_export_channel_messages_sends_text_file(self) -> None:
        class FakeTextChannel:
            id = 10
            name = "general"

            async def history(self, *, limit=None, oldest_first=False):
                self.seen_limit = limit
                self.seen_oldest_first = oldest_first
                yield SimpleNamespace(
                    created_at=datetime(2026, 7, 29, 1, 2, 3, tzinfo=timezone.utc),
                    author=SimpleNamespace(id=123, display_name="admin", name="admin"),
                    content="hello",
                    attachments=[],
                )

        channel = FakeTextChannel()
        bot = SimpleNamespace(latency=0.1)
        cog = SlashCommands(bot)
        response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        followup = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            response=response,
            followup=followup,
            user=SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=True)),
            channel=channel,
        )

        with patch.object(slash_commands_module.discord, "TextChannel", FakeTextChannel):
            await SlashCommands.export_channel_messages.callback(cog, interaction, None, 50)

        response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        followup.send.assert_awaited_once()
        kwargs = followup.send.await_args.kwargs
        self.assertIn("履歴を 1 件出力しました", kwargs["content"])
        self.assertEqual(kwargs["file"].filename.startswith("general-messages-"), True)
        self.assertEqual(channel.seen_limit, 50)
        self.assertEqual(channel.seen_oldest_first, True)

    async def test_build_channel_export_text_includes_thread_messages(self) -> None:
        bot = SimpleNamespace(latency=0.1)
        cog = SlashCommands(bot)

        class FakeThread:
            id = 20
            name = "topic-thread"

            async def history(self, *, limit=None, oldest_first=False):
                yield SimpleNamespace(
                    created_at=datetime(2026, 7, 29, 2, 0, 0, tzinfo=timezone.utc),
                    author=SimpleNamespace(id=222, display_name="thread-user", name="thread-user"),
                    content="thread body",
                    attachments=[],
                )

        class FakeTextChannel:
            id = 10
            name = "general"

            def __init__(self) -> None:
                self.threads = [FakeThread()]

            async def history(self, *, limit=None, oldest_first=False):
                yield SimpleNamespace(
                    created_at=datetime(2026, 7, 29, 1, 0, 0, tzinfo=timezone.utc),
                    author=SimpleNamespace(id=111, display_name="channel-user", name="channel-user"),
                    content="channel body",
                    attachments=[],
                )

            async def archived_threads(self, *, limit=None, private=False):
                if False:
                    yield None

        text, count, truncated = await cog._build_channel_export_text(FakeTextChannel())

        self.assertEqual(count, 2)
        self.assertFalse(truncated)
        self.assertIn("[channel messages]", text)
        self.assertIn("channel-user (111): channel body", text)
        self.assertIn("[thread messages] #topic-thread (20)", text)
        self.assertIn("thread-user (222): thread body", text)

    async def test_raw_reaction_add_handles_vc_panel_join(self) -> None:
        class FakeTextChannel:
            id = 20

            def __init__(self) -> None:
                self.send = AsyncMock()

        class FakeVoiceChannel:
            id = 30

        class FakeMember:
            id = 40
            mention = "<@40>"
            bot = False

            def __init__(self) -> None:
                self.voice = SimpleNamespace(channel=FakeVoiceChannel())
                self.guild_permissions = SimpleNamespace(move_members=True)

        member = FakeMember()
        guild = SimpleNamespace(
            id=10,
            get_member=Mock(return_value=member),
            get_channel=Mock(return_value=FakeVoiceChannel()),
            me=SimpleNamespace(id=999),
        )
        channel = FakeTextChannel()
        bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            get_guild=Mock(return_value=guild),
            get_channel=Mock(return_value=channel),
            latency=0.1,
        )
        cog = SlashCommands(bot)
        cog._vc_panels[100] = VcPanelState(
            guild_id=10,
            channel_id=20,
            voice_channel_id=30,
            host_user_id=40,
        )
        payload = SimpleNamespace(
            user_id=40,
            message_id=100,
            channel_id=20,
            guild_id=10,
            emoji=cog.VC_JOIN_EMOJI,
        )

        with (
            patch.object(slash_commands_module.discord, "Member", FakeMember),
            patch.object(slash_commands_module.discord, "TextChannel", FakeTextChannel),
            patch.object(slash_commands_module.discord, "VoiceChannel", FakeVoiceChannel),
        ):
            await cog.on_raw_reaction_add(payload)

        self.assertIn(40, cog._vc_panels[100].joined_user_ids)
        channel.send.assert_awaited_once()
        self.assertIn("参加登録しました", channel.send.await_args.args[0])

    async def test_raw_reaction_add_handles_vc_panel_mute(self) -> None:
        class FakeRole:
            def __ge__(self, _other):
                return False

        class FakeTextChannel:
            id = 20

            def __init__(self) -> None:
                self.send = AsyncMock()

        class FakeVoiceChannel:
            id = 30

        class FakeMember:
            id = 40
            mention = "<@40>"
            bot = False
            top_role = FakeRole()

            def __init__(self) -> None:
                self.voice = SimpleNamespace(channel=FakeVoiceChannel())
                self.guild_permissions = SimpleNamespace(move_members=True)
                self.edit = AsyncMock()

        member = FakeMember()
        me = SimpleNamespace(
            id=999,
            top_role=object(),
            guild_permissions=SimpleNamespace(mute_members=True, deafen_members=True),
        )
        guild = SimpleNamespace(
            id=10,
            get_member=Mock(return_value=member),
            get_channel=Mock(return_value=FakeVoiceChannel()),
            me=me,
        )
        channel = FakeTextChannel()
        bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            get_guild=Mock(return_value=guild),
            get_channel=Mock(return_value=channel),
            latency=0.1,
        )
        cog = SlashCommands(bot)
        cog._vc_panels[100] = VcPanelState(
            guild_id=10,
            channel_id=20,
            voice_channel_id=30,
            host_user_id=40,
            joined_user_ids={40},
        )
        payload = SimpleNamespace(
            user_id=40,
            message_id=100,
            channel_id=20,
            guild_id=10,
            emoji=cog.VC_MUTE_ON_EMOJI,
        )

        with (
            patch.object(slash_commands_module.discord, "Member", FakeMember),
            patch.object(slash_commands_module.discord, "TextChannel", FakeTextChannel),
            patch.object(slash_commands_module.discord, "VoiceChannel", FakeVoiceChannel),
        ):
            await cog.on_raw_reaction_add(payload)

        member.edit.assert_awaited_once()
        self.assertTrue(member.edit.await_args.kwargs["mute"])
        channel.send.assert_awaited_once()
        self.assertIn("成功 1 / 失敗 0", channel.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
