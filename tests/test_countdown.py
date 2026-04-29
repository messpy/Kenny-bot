from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "discord" not in sys.modules:
    class _DiscordModule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    class _DiscordSubmodule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    discord = _DiscordModule("discord")

    class _AllowedMentions:
        @staticmethod
        def none():
            return None

    discord.AllowedMentions = _AllowedMentions
    discord.abc = _DiscordSubmodule("discord.abc")
    discord.abc.Messageable = object
    sys.modules["discord"] = discord
    sys.modules["discord.abc"] = discord.abc

from src.kennybot.utils.countdown import ChannelCountdown


class _FakeMessage:
    def __init__(self) -> None:
        self.deleted = False
        self.edits: list[str] = []

    async def delete(self) -> None:
        self.deleted = True

    async def edit(self, *, content: str, allowed_mentions=None) -> None:
        self.edits.append(content)


class _SlowSendChannel:
    def __init__(self) -> None:
        self.message = _FakeMessage()
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def send(self, content: str, allowed_mentions=None) -> _FakeMessage:
        self.send_started.set()
        await self.release_send.wait()
        return self.message


class CountdownTests(unittest.TestCase):
    def test_stop_deletes_progress_message_even_if_cancelled_during_send(self) -> None:
        async def scenario() -> None:
            countdown = ChannelCountdown()
            channel = _SlowSendChannel()
            task = asyncio.create_task(
                countdown._run_countup(
                    key="progress",
                    base_text="",
                    text_factory=lambda elapsed: f"tick {elapsed}",
                    mention_user_id=None,
                    elapsed=1,
                    channel=channel,
                    initial_delay_seconds=0,
                )
            )
            countdown._tasks["progress"] = task

            await asyncio.sleep(1.05)
            await channel.send_started.wait()

            stop_task = asyncio.create_task(
                countdown.stop("progress", delete_message=True)
            )
            channel.release_send.set()

            await stop_task
            await asyncio.sleep(0)

            self.assertTrue(channel.message.deleted)
            self.assertIsNone(countdown.get_message("progress"))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
