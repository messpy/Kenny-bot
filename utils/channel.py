# utils/channel.py
# チャンネル解決

import logging
from typing import Optional

import discord
from discord.utils import get as get_discord_obj

from .config import CHANNEL_NAMES


logger = logging.getLogger(__name__)


def resolve_log_channel(guild: discord.Guild, kind: str) -> Optional[discord.TextChannel]:
    """
    ログ用チャンネルを名前から解決

    Args:
        guild: Discord Guild
        kind: "member", "voice", "other", "bot"

    Returns:
        TextChannel or None
    """
    if guild is None:
        return None

    channel_name = CHANNEL_NAMES.get(kind)
    if not channel_name:
        logger.warning("resolve_log_channel: unknown kind=%s", kind)
        return None

    ch = get_discord_obj(guild.text_channels, name=channel_name)
    if isinstance(ch, discord.TextChannel):
        return ch

    logger.warning("log channel(kind=%s) not found: name=%s", kind, channel_name)
    return None
