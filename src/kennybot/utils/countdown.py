from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import discord


class ChannelCountdown:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._messages: dict[str, discord.Message] = {}

    def get_message(self, key: str) -> discord.Message | None:
        return self._messages.get(key)

    def _format_elapsed(self, seconds: int) -> str:
        total = max(1, int(seconds))
        minutes, secs = divmod(total, 60)
        if minutes <= 0:
            return f"{secs}秒"
        return f"{minutes}分{secs}秒"

    async def stop(self, key: str, *, delete_message: bool = False) -> None:
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
        msg = self._messages.pop(key, None)
        if delete_message and msg is not None:
            try:
                await msg.delete()
            except Exception:
                pass

    async def start_countup(
        self,
        *,
        key: str,
        channel: discord.abc.Messageable,
        base_text: str = "",
        text_factory: Callable[[int], str] | None = None,
        mention_user_id: int | None = None,
        start_seconds: int = 1,
    ) -> None:
        await self.stop(key, delete_message=True)
        prefix = f"<@{mention_user_id}> " if mention_user_id else ""
        elapsed = max(1, int(start_seconds))
        text = text_factory(elapsed) if text_factory is not None else f"{base_text} {self._format_elapsed(elapsed)}"
        msg = await channel.send(
            f"{prefix}{text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._messages[key] = msg
        task = asyncio.create_task(
            self._run_countup(
                key=key,
                msg=msg,
                base_text=base_text,
                text_factory=text_factory,
                mention_user_id=mention_user_id,
                elapsed=elapsed,
            )
        )
        self._tasks[key] = task

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
        self._messages[key] = msg
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
            self._messages.pop(key, None)

    async def _run_countup(
        self,
        *,
        key: str,
        msg: discord.Message,
        base_text: str,
        text_factory: Callable[[int], str] | None,
        mention_user_id: int | None,
        elapsed: int,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                elapsed += 1
                prefix = f"<@{mention_user_id}> " if mention_user_id else ""
                text = text_factory(elapsed) if text_factory is not None else f"{base_text} {self._format_elapsed(elapsed)}"
                await msg.edit(content=f"{prefix}{text}", allowed_mentions=discord.AllowedMentions.none())
        except asyncio.CancelledError:
            return
        finally:
            self._tasks.pop(key, None)
            self._messages.pop(key, None)
