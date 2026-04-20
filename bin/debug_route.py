#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.kennybot.bootstrap import create_bot
from src.kennybot.utils.env import load_env_file
import src.kennybot.cogs.message_logger as message_logger_module
import src.kennybot.cogs.slash_commands as slash_commands_module
import src.kennybot.cogs.member_logger as member_logger_module
import src.kennybot.cogs.audit_logger as audit_logger_module
import src.kennybot.cogs.reaction_roles as reaction_roles_module
import src.kennybot.cogs.voice_logger as voice_logger_module
import src.kennybot.guards.mod_actions as mod_actions_module
from src.kennybot.utils.message_store import MessageStore


class _NoopAsyncContext:
    async def __aenter__(self) -> "_NoopAsyncContext":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class CaptureChannel:
    def __init__(self, channel_id: int, name: str = "debug-channel") -> None:
        self.id = int(channel_id)
        self.name = name
        self.messages: list[dict[str, Any]] = []

    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        entry: dict[str, Any] = {
            "content": content,
            "kwargs": kwargs,
        }
        embed = kwargs.get("embed")
        if embed is not None:
            try:
                entry["embed"] = embed.to_dict()
            except Exception:
                entry["embed"] = {
                    "title": getattr(embed, "title", ""),
                    "description": getattr(embed, "description", ""),
                }
        self.messages.append(entry)
        return SimpleNamespace(id=len(self.messages), content=content)

    def typing(self) -> _NoopAsyncContext:
        return _NoopAsyncContext()


class FakeRole:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeActivity:
    def __init__(self, name: str, activity_type: str = "playing") -> None:
        self.name = name
        self.type = activity_type


class FakeMember:
    def __init__(
        self,
        user_id: int,
        name: str,
        *,
        display_name: str | None = None,
        bot: bool = False,
        nick: str | None = None,
        roles: list[FakeRole] | None = None,
        activities: list[FakeActivity] | None = None,
        status: str = "online",
    ) -> None:
        self.id = int(user_id)
        self.name = name
        self.display_name = display_name or name
        self.bot = bot
        self.nick = nick
        self.roles = roles or []
        self.activities = activities or []
        self.status = status
        self.joined_at = None
        self.premium_since = None
        self.created_at = None

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class FakeGuild:
    def __init__(self, guild_id: int, name: str, members: list[FakeMember]) -> None:
        self.id = int(guild_id)
        self.name = name
        self._members = {member.id: member for member in members}
        self.text_channels: list[CaptureChannel] = []
        self.threads: list[Any] = []

    def get_member(self, user_id: int) -> FakeMember | None:
        return self._members.get(int(user_id))

    async def fetch_member(self, user_id: int) -> FakeMember:
        member = self._members.get(int(user_id))
        if member is None:
            raise discord.NotFound(response=None, message="member not found")
        return member


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int,
        content: str,
        author: FakeMember,
        guild: FakeGuild,
        channel: CaptureChannel,
        mentions: list[FakeMember] | None = None,
        reference: Any | None = None,
    ) -> None:
        self.id = int(message_id)
        self.content = content
        self.author = author
        self.guild = guild
        self.channel = channel
        self.mentions = mentions or []
        self.reference = reference
        self.webhook_id = None


class FakeInteractionResponse:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._done = False
        self._sink = sink

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True
        self._sink.append({"type": "defer", "ephemeral": ephemeral, "thinking": thinking})

    async def send_message(self, content: Any = None, **kwargs: Any) -> None:
        self._done = True
        self._sink.append({"type": "response", "content": content, "kwargs": kwargs})


class FakeInteractionFollowup:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        self._sink.append({"type": "followup", "content": content, "kwargs": kwargs})
        return SimpleNamespace(id=len(self._sink), content=content)


class FakeInteraction:
    def __init__(self, *, bot: discord.Client, guild: FakeGuild, channel: CaptureChannel, user: FakeMember) -> None:
        self.client = bot
        self.guild = guild
        self.channel = channel
        self.channel_id = channel.id
        self.user = user
        self.response_events: list[dict[str, Any]] = []
        self.response = FakeInteractionResponse(self.response_events)
        self.followup = FakeInteractionFollowup(self.response_events)
        self.command = SimpleNamespace(qualified_name="debug")


def _noop_async_send_event_log(*args: Any, **kwargs: Any) -> None:
    return None


def _noop_add_message(self: MessageStore, *args: Any, **kwargs: Any) -> None:
    return None


def _noop_log(*args: Any, **kwargs: Any) -> None:
    return None
async def _run_mention_preview(args: argparse.Namespace) -> int:
    bot = create_bot()
    await bot.setup_hook()
    bot._connection.user = FakeMember(args.bot_user_id, args.bot_user_name, bot=True)
    bot.process_commands = lambda msg: asyncio.sleep(0)  # type: ignore[assignment]
    bot.spam_guard.allow_message = lambda *a, **k: True  # type: ignore[assignment]
    bot.spam_guard.allow_ai = lambda *a, **k: True  # type: ignore[assignment]

    message_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    message_logger_module.log_user_message = _noop_log  # type: ignore[assignment]
    message_logger_module.log_ai_output = _noop_log  # type: ignore[assignment]
    message_logger_module.log_system_event = _noop_log  # type: ignore[assignment]
    slash_commands_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    member_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    audit_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    reaction_roles_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    voice_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    mod_actions_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    MessageStore.add_message = _noop_add_message  # type: ignore[assignment]

    capture_channel = CaptureChannel(args.channel_id, name=args.channel_name)
    guild = FakeGuild(
        args.guild_id,
        args.guild_name,
        members=[
            bot._connection.user,
            FakeMember(args.author_id, args.author_name, display_name=args.author_display_name),
        ],
    )
    guild.text_channels = [capture_channel]
    author = guild.get_member(args.author_id)
    assert author is not None
    bot_member = bot._connection.user
    mentions = [bot_member]
    if args.mention_user_id:
        mentions.append(
            FakeMember(
                args.mention_user_id,
                args.mention_user_name or f"user-{args.mention_user_id}",
                display_name=args.mention_user_display_name or args.mention_user_name or f"user-{args.mention_user_id}",
            )
        )

    msg = FakeMessage(
        message_id=args.message_id,
        content=args.text,
        author=author,
        guild=guild,
        channel=capture_channel,
        mentions=mentions,
    )
    cog = bot.get_cog("MessageLogger")
    if cog is None:
        raise RuntimeError("MessageLogger cog not available")
    cog._schedule_message_index = lambda *a, **k: None  # type: ignore[assignment]
    if args.no_ai:
        text = args.text
        lowered = text.lower()
        route = "chat"
        if cog._is_runtime_model_query(text):
            route = "runtime_model"
        elif cog._is_capability_query(text):
            route = "capability"
        elif message_logger_module.is_current_info_intent(text) or message_logger_module.is_search_intent(text):
            route = "web_search"
        elif cog._is_bot_capability_or_game_query(text):
            route = "capability_grounded_chat"
        print("=== mention routing preview ===")
        print(f"route={route}")
        print(f"text={text!r}")
        print(f"mentions={[m.id for m in mentions]!r}")
        return 0
    await cog.on_message(msg)

    print("=== captured messages ===")
    for idx, entry in enumerate(capture_channel.messages, start=1):
        print(f"[{idx}] {entry.get('content')!r}")
        embed = entry.get("embed")
        if embed:
            title = embed.get("title", "")
            desc = embed.get("description", "")
            print(f"    embed.title={title!r}")
            print(f"    embed.description={desc!r}")
    return 0


async def _run_slash_preview(args: argparse.Namespace) -> int:
    bot = create_bot()
    await bot.setup_hook()
    bot._connection.user = FakeMember(args.bot_user_id, args.bot_user_name, bot=True)

    slash_commands_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    slash_commands_module.log_user_message = _noop_log  # type: ignore[assignment]
    message_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    message_logger_module.log_user_message = _noop_log  # type: ignore[assignment]
    message_logger_module.log_ai_output = _noop_log  # type: ignore[assignment]
    message_logger_module.log_system_event = _noop_log  # type: ignore[assignment]
    member_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    audit_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    reaction_roles_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    voice_logger_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]
    mod_actions_module.send_event_log = _noop_async_send_event_log  # type: ignore[assignment]

    capture_channel = CaptureChannel(args.channel_id, name=args.channel_name)
    guild = FakeGuild(
        args.guild_id,
        args.guild_name,
        members=[bot._connection.user, FakeMember(args.author_id, args.author_name, display_name=args.author_display_name)],
    )
    guild.text_channels = [capture_channel]
    user = guild.get_member(args.author_id)
    assert user is not None
    interaction = FakeInteraction(bot=bot, guild=guild, channel=capture_channel, user=user)
    command = bot.tree.get_command(args.command)
    if command is None:
        raise SystemExit(f"Unknown slash command: {args.command}")

    kwargs = {}
    preset_args = {
        "help": {},
        "bot_info": {},
        "model_list": {},
        "config_show": {},
        "minutes_status": {},
    }
    if args.preset and not args.args_json:
        kwargs = dict(preset_args.get(args.preset, {}))
    if args.args_json:
        try:
            parsed = json.loads(args.args_json)
            if isinstance(parsed, dict):
                kwargs = parsed
        except Exception as e:
            raise SystemExit(f"Invalid --args-json: {e}") from e

    await command.callback(command.binding, interaction, **kwargs)

    print("=== slash response events ===")
    for idx, event in enumerate(interaction.response_events, start=1):
        print(f"[{idx}] {event}")
    print("=== captured channel messages ===")
    for idx, entry in enumerate(capture_channel.messages, start=1):
        print(f"[{idx}] {entry.get('content')!r}")
        embed = entry.get("embed")
        if embed:
            print(f"    embed.title={embed.get('title', '')!r}")
            print(f"    embed.description={embed.get('description', '')!r}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kenny Bot route preview helper")
    sub = parser.add_subparsers(dest="mode", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--guild-id", type=int, default=664237144600215581)
    base.add_argument("--guild-name", type=str, default="debug-guild")
    base.add_argument("--channel-id", type=int, default=1005826751391342663)
    base.add_argument("--channel-name", type=str, default="debug-channel")
    base.add_argument("--author-id", type=int, default=1190939100514103357)
    base.add_argument("--author-name", type=str, default="debug-user")
    base.add_argument("--author-display-name", type=str, default="debug-user")
    base.add_argument("--bot-user-id", type=int, default=387651883847909376)
    base.add_argument("--bot-user-name", type=str, default="Kennybot")

    p_mention = sub.add_parser("mention", parents=[base], help="Preview a mention/message response")
    p_mention.add_argument("text", type=str)
    p_mention.add_argument("--message-id", type=int, default=1)
    p_mention.add_argument("--mention-user-id", type=int, default=0)
    p_mention.add_argument("--mention-user-name", type=str, default="")
    p_mention.add_argument("--mention-user-display-name", type=str, default="")
    p_mention.add_argument("--no-ai", action="store_true", help="Only print the routing decision")

    p_slash = sub.add_parser("slash", parents=[base], help="Preview a slash command callback")
    p_slash.add_argument("command", type=str)
    p_slash.add_argument("--args-json", type=str, default="")
    p_slash.add_argument(
        "--preset",
        type=str,
        default="",
        choices=["", "help", "bot_info", "model_list", "config_show", "minutes_status"],
        help="Use a built-in argument preset when args-json is omitted",
    )

    return parser


def main() -> int:
    load_env_file()
    parser = _build_parser()
    args = parser.parse_args()
    if args.mode == "mention":
        return asyncio.run(_run_mention_preview(args))
    if args.mode == "slash":
        return asyncio.run(_run_slash_preview(args))
    raise SystemExit(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
