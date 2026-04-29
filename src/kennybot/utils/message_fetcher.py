import asyncio
import logging
from collections import OrderedDict
from datetime import datetime
from typing import Optional

import discord
from src.kennybot.utils.time import now_jst

logger = logging.getLogger(__name__)


class MessageFetcher:
    _instance: Optional["MessageFetcher"] = None

    def __init__(self):
        self._cache: OrderedDict[int, list[discord.Message]] = OrderedDict()
        self._cache_ttl_sec = 30
        self._cache_max_channels = 10

    @classmethod
    def get_instance(cls) -> "MessageFetcher":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _cache_key(self, channel_id: int) -> int:
        return channel_id

    def _get_cached(self, channel_id: int) -> Optional[list[discord.Message]]:
        key = self._cache_key(channel_id)
        if key in self._cache:
            cached, timestamp = self._cache[key]
            if (now_jst() - timestamp).total_seconds() < self._cache_ttl_sec:
                return cached
            del self._cache[key]
        return None

    def _set_cached(self, channel_id: int, messages: list[discord.Message]) -> None:
        key = self._cache_key(channel_id)
        if len(self._cache) >= self._cache_max_channels:
            self._cache.popitem(last=False)
        self._cache[key] = (messages, now_jst())

    def invalidate(self, channel_id: int) -> None:
        key = self._cache_key(channel_id)
        if key in self._cache:
            del self._cache[key]

    async def fetch_recent(
        self,
        channel: discord.abc.Messageable,
        count: int,
        use_cache: bool = True,
    ) -> list[discord.Message]:
        if count <= 0:
            raise ValueError("count must be > 0")

        if use_cache and isinstance(channel, discord.TextChannel):
            cached = self._get_cached(channel.id)
            if cached is not None and len(cached) >= count:
                return cached[:count]

        messages = []
        try:
            async for msg in channel.history(limit=count, oldest_first=False):
                messages.append(msg)
            messages = list(reversed(messages))
        except Exception:
            logger.exception("Failed to fetch messages from channel %s", channel.id)

        if use_cache and isinstance(channel, discord.TextChannel) and messages:
            self._set_cached(channel.id, messages)

        return messages

    async def fetch_user_recent(
        self,
        channel: discord.abc.Messageable,
        user_id: int,
        count: int,
        search_limit: int = 500,
    ) -> list[discord.Message]:
        if count <= 0:
            raise ValueError("count must be > 0")
        if search_limit <= 0:
            raise ValueError("search_limit must be > 0")

        messages = []
        try:
            async for msg in channel.history(limit=search_limit, oldest_first=False):
                if msg.author.id == user_id:
                    messages.append(msg)
                    if len(messages) >= count:
                        break
            messages = list(reversed(messages))
        except Exception:
            logger.exception("Failed to fetch user messages from channel %s", channel.id)

        return messages

    async def fetch_for_context(
        self,
        channel: discord.abc.Messageable,
        count: int = 20,
        exclude_bot: bool = True,
    ) -> list[discord.Message]:
        all_msgs = await self.fetch_recent(channel, count * 3, use_cache=False)
        result = []
        for msg in all_msgs:
            if exclude_bot and msg.author.bot:
                continue
            result.append(msg)
            if len(result) >= count:
                break
        return result


def to_record(message: discord.Message) -> dict:
    return {
        "message_id": message.id,
        "author_id": message.author.id,
        "author_name": str(message.author),
        "content": message.content,
        "created_at": message.created_at.isoformat(),
        "channel_id": message.channel.id,
    }


def format_messages_for_context(messages: list[discord.Message]) -> str:
    if not messages:
        return ""

    context_lines = []
    for msg in messages:
        author_display = str(msg.author)
        if msg.author.id:
            author_display = f"{msg.author} ({msg.author.id})"
        time_str = ""
        created_at = getattr(msg, "created_at", None)
        if created_at:
            try:
                dt = created_at.astimezone(JST)
                time_str = dt.strftime("%H:%M")
            except Exception:
                pass
        if time_str:
            context_lines.append(f"[{time_str}] {author_display}: {msg.content}")
        else:
            context_lines.append(f"{author_display}: {msg.content}")
    return "\n".join(context_lines)
