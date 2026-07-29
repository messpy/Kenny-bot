from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch
import unittest

from src.kennybot.cogs import slash_commands as slash_commands_module
from src.kennybot.cogs.slash_commands import SlashCommands


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


if __name__ == "__main__":
    unittest.main()
