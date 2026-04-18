import logging
import os
from typing import Iterable

import discord

from utils.runtime_settings import get_settings


logger = logging.getLogger(__name__)
_settings = get_settings()


def _as_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _level_color(level: str) -> discord.Color:
    normalized = (level or "info").lower()
    if normalized in {"error", "critical"}:
        return discord.Color.red()
    if normalized == "warning":
        return discord.Color.orange()
    if normalized == "success":
        return discord.Color.green()
    return discord.Color.blurple()


async def resolve_event_log_channel(
    bot: discord.Client,
    guild: discord.Guild | None = None,
) -> discord.TextChannel | None:
    channel_id = 0
    if guild is not None:
        channel_id = _as_int(_settings.get("logging.event_channel_id", 0, guild_id=guild.id))
    if channel_id <= 0:
        channel_id = _as_int(_settings.get("logging.event_channel_id", 0))
    if channel_id <= 0:
        channel_id = _as_int(os.getenv("DISCORD_EVENT_LOG_CHANNEL_ID"))

    if channel_id > 0:
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                logger.exception("Failed to fetch event log channel: %s", channel_id)
                channel = None
        if isinstance(channel, discord.TextChannel):
            return channel
        logger.warning("Configured event log channel is not a text channel: %s", channel_id)

    if guild is None:
        return None

    channel_name = str(_settings.get("logging.event_channel_name", "kennybot-log", guild_id=guild.id) or "").strip()
    if not channel_name:
        channel_name = str(_settings.get("logging.event_channel_name", "kennybot-log") or "").strip()
    if not channel_name:
        return None

    resolved = discord.utils.get(guild.text_channels, name=channel_name)
    if isinstance(resolved, discord.TextChannel):
        return resolved
    return None


async def send_event_log(
    bot: discord.Client,
    *,
    title: str,
    description: str = "",
    guild: discord.Guild | None = None,
    level: str = "info",
    fields: Iterable[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
    source_channel_id: int | None = None,
) -> discord.Message | None:
    if source_channel_id is None:
        return None
    channel = await resolve_event_log_channel(bot, guild)
    if channel is None:
        return None
    if source_channel_id is not None and int(source_channel_id) == int(getattr(channel, "id", 0)):
        return None

    embed = discord.Embed(
        title=title,
        description=description,
        color=_level_color(level),
        timestamp=discord.utils.utcnow(),
    )
    for name, value, inline in fields or ():
        if value:
            embed.add_field(name=name, value=value[:1024], inline=inline)
    if footer:
        embed.set_footer(text=footer[:2048])

    try:
        return await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.exception("Failed to send event log: %s", title)
        return None
