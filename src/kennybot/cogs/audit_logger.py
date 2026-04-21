from __future__ import annotations

import discord
from discord.ext import commands

from src.kennybot.utils.event_logger import send_event_log


def _fmt_channel(channel: discord.abc.GuildChannel | None) -> str:
    if channel is None:
        return "None"
    return f"{channel.name} ({channel.id})"


def _truncate(value: str, limit: int = 1000) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


class AuditLogger(commands.Cog):
    """監査向けイベントログ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.application_command:
            return
        guild = interaction.guild
        data = interaction.data if isinstance(interaction.data, dict) else {}
        name = str(data.get("name") or "unknown")
        options = data.get("options") or []
        option_text = _truncate(str(options), 1000)
        user = interaction.user
        await send_event_log(
            self.bot,
            guild=guild,
            title="Slash Command 実行",
            description=f"/{name} が実行されました。",
            fields=[
                ("ユーザー", f"{user} ({user.id})", False),
                ("チャンネル", _fmt_channel(interaction.channel) if isinstance(interaction.channel, discord.abc.GuildChannel) else str(getattr(interaction.channel, 'id', 'DM')), False),
                ("オプション", option_text or "[]", False),
            ],
            source_channel_id=interaction.channel_id,
            channel_kind="other",
        )
