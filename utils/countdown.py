from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import discord


class ChannelCountdown:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start_or_replace(
        self,
        *,
        key: str,
        channel: discord.abc.Messageable,
        initial_text: str,
        total_seconds: int,
        mention_user_id: int | None = None,
        done_text: str | None = None,
        on_done: Callable[[discord.Message], Awaitable[None] | None] | None = None,
    ) -> None:
        old = self._tasks.pop(key, None)
        if old is not None:
            old.cancel()

        task = asyncio.create_task(
            self._run(
                key=key,
                channel=channel,
                initial_text=initial_text,
                total_seconds=total_seconds,
                mention_user_id=mention_user_id,
                done_text=done_text,
                on_done=on_done,
            )
        )
        self._tasks[key] = task

    async def _run(
        self,
        *,
        key: str,
        channel: discord.abc.Messageable,
        initial_text: str,
        total_seconds: int,
        mention_user_id: int | None,
        done_text: str | None,
        on_done: Callable[[discord.Message], Awaitable[None] | None] | None,
    ) -> None:
        prefix = f"<@{mention_user_id}> " if mention_user_id else ""
        msg = await channel.send(f"{prefix}{initial_text}", allowed_mentions=discord.AllowedMentions.none())
        remain = max(0, int(total_seconds))
        try:
            while remain > 0:
                step = 1 if remain <= 10 else min(10, remain)
                await asyncio.sleep(step)
                remain -= step
                if remain > 0:
                    await msg.edit(content=f"{prefix}⏳ 残り {remain} 秒", allowed_mentions=discord.AllowedMentions.none())
                elif done_text:
                    await msg.edit(content=f"{prefix}{done_text}", allowed_mentions=discord.AllowedMentions.none())
                    if on_done is not None:
                        out = on_done(msg)
                        if hasattr(out, "__await__"):
                            await out
        except asyncio.CancelledError:
            return
        finally:
            self._tasks.pop(key, None)
