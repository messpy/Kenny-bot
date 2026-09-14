# utils/channel.py
# チャンネル解決

import logging
from typing import Optional

import discord
from discord.utils import get as get_discord_obj

from .app_constants import CHANNEL_NAMES


logger = logging.getLogger(__name__)

# 旧構成では単数形 `voice-event` で作られているサーバーがあるため、
# canonical name (`voice-events`) が見つからない場合だけ互換名も探す。
CHANNEL_NAME_ALIASES = {
    "voice": ("voice-event",),
}


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
    if ch is None:
        for alias in CHANNEL_NAME_ALIASES.get(kind, ()):
            ch = get_discord_obj(guild.text_channels, name=alias)
            if ch is not None:
                logger.info(
                    "log channel(kind=%s) resolved using compatibility name=%s",
                    kind,
                    alias,
                )
                break
    if isinstance(ch, discord.TextChannel):
        return ch

    logger.warning("log channel(kind=%s) not found: name=%s", kind, channel_name)
    return None
