#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.kennybot.bootstrap import create_bot
from src.kennybot.utils.env import load_env_file
from src.kennybot.utils.paths import DEBUG_ROUTE_HISTORY_DIR, ROOT_DIR
from src.kennybot.utils.profile_preview import build_channel_profile_preview
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


@dataclass(slots=True)
class PreviewSendPolicy:
    """Preview 中の送信の扱いをまとめる。

    deliver=False のときは、実行フローはそのままに送信先への配送だけ抑止する。
    """

    deliver: bool = True


class CaptureChannel:
    def __init__(
        self,
        channel_id: int,
        name: str = "debug-channel",
        history_messages: list[Any] | None = None,
        *,
        send_policy: PreviewSendPolicy | None = None,
    ) -> None:
        self.id = int(channel_id)
        self.name = name
        self.messages: list[dict[str, Any]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.history_messages: list[Any] = list(history_messages or [])
        self.send_policy = send_policy or PreviewSendPolicy()

    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        entry: dict[str, Any] = {
            "content": content,
            "kwargs": kwargs,
            "suppressed": not self.send_policy.deliver,
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
        self.events.append(
            {
                "type": "send",
                "content": content,
                "kwargs": kwargs,
                "suppressed": not self.send_policy.deliver,
                "embed": entry.get("embed"),
            }
        )
        if self.send_policy.deliver:
            self.sent_messages.append(entry)
        return CaptureSentMessage(self, entry)

    def typing(self) -> _NoopAsyncContext:
        return _NoopAsyncContext()

    async def history(self, limit: int = 100, oldest_first: bool = False):
        items = self.history_messages[:]
        if limit is not None and int(limit) > 0:
            items = items[-int(limit):]
        if not oldest_first:
            items = list(reversed(items))
        for item in items:
            yield item


class CaptureSentMessage:
    def __init__(self, channel: CaptureChannel, entry: dict[str, Any]) -> None:
        self._channel = channel
        self._entry = entry
        self.id = len(channel.messages)
        self.content = entry.get("content")

    async def edit(self, **kwargs: Any) -> Any:
        if "content" in kwargs:
            self._entry["content"] = kwargs["content"]
            self.content = kwargs["content"]
        if "embed" in kwargs and kwargs["embed"] is not None:
            embed = kwargs["embed"]
            try:
                self._entry["embed"] = embed.to_dict()
            except Exception:
                self._entry["embed"] = {
                    "title": getattr(embed, "title", ""),
                    "description": getattr(embed, "description", ""),
                }
        if "kwargs" in kwargs:
            self._entry.setdefault("edit_kwargs", {}).update(kwargs)
        self._channel.events.append(
            {
                "type": "edit",
                "message_id": self.id,
                "content": self.content,
                "kwargs": kwargs,
                "embed": self._entry.get("embed"),
            }
        )
        return self

    async def delete(self) -> None:
        self._channel.events.append(
            {
                "type": "delete",
                "message_id": self.id,
                "content": self.content,
            }
        )
        return None


class FakeRole:
    def __init__(self, name: str) -> None:
        self.name = name


class FakePermissions:
    def __init__(self, *, kick_members: bool = True, ban_members: bool = True) -> None:
        self.kick_members = kick_members
        self.ban_members = ban_members


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
        self.guild_permissions = FakePermissions()
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
        self.me: FakeMember | None = None
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
        self.created_at = datetime.now(timezone(timedelta(hours=9)))


class FakeInteractionResponse:
    def __init__(self, sink: list[dict[str, Any]], *, send_policy: PreviewSendPolicy | None = None) -> None:
        self._done = False
        self._sink = sink
        self._send_policy = send_policy or PreviewSendPolicy()

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True
        self._sink.append({"type": "defer", "ephemeral": ephemeral, "thinking": thinking})

    async def send_message(self, content: Any = None, **kwargs: Any) -> None:
        self._done = True
        self._sink.append(
            {
                "type": "response",
                "content": content,
                "kwargs": kwargs,
                "suppressed": not self._send_policy.deliver,
            }
        )


class FakeInteractionFollowup:
    def __init__(self, sink: list[dict[str, Any]], *, send_policy: PreviewSendPolicy | None = None) -> None:
        self._sink = sink
        self._send_policy = send_policy or PreviewSendPolicy()

    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        self._sink.append(
            {
                "type": "followup",
                "content": content,
                "kwargs": kwargs,
                "suppressed": not self._send_policy.deliver,
            }
        )
        return SimpleNamespace(id=len(self._sink), content=content)


class FakeInteraction:
    def __init__(
        self,
        *,
        bot: discord.Client,
        guild: FakeGuild,
        channel: CaptureChannel,
        user: FakeMember,
        send_policy: PreviewSendPolicy | None = None,
    ) -> None:
        self.client = bot
        self.guild = guild
        self.channel = channel
        self.channel_id = channel.id
        self.user = user
        self.response_events: list[dict[str, Any]] = []
        self.response = FakeInteractionResponse(self.response_events, send_policy=send_policy)
        self.followup = FakeInteractionFollowup(self.response_events, send_policy=send_policy)
        self.command = SimpleNamespace(qualified_name="debug")


async def _noop_async_send_event_log(*args: Any, **kwargs: Any) -> None:
    return None


def _noop_add_message(self: MessageStore, *args: Any, **kwargs: Any) -> None:
    return None


def _noop_log(*args: Any, **kwargs: Any) -> None:
    return None


def _truncate_text(value: Any, limit: int = 240) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...(省略)"


def _format_user_response_pair(*, user_id: int, user_text: str, bot_id: int | None = None, bot_text: str = "") -> str:
    bot_mention = f"<@{bot_id}>" if bot_id else "<@bot>"
    lines = [
        f"【ユーザー】<@{user_id}> {user_text}".rstrip(),
        f"【応答】{bot_mention} {bot_text}".rstrip(),
    ]
    return "\n".join(lines)


def _print_capture_events(events: list[dict[str, Any]], *, bot_id: int | None, user_id: int, user_text: str) -> None:
    if not events:
        print("<no events>", flush=True)
        return
    for idx, event in enumerate(events, start=1):
        event_type = str(event.get("type") or "event")
        content = str(event.get("content") or "")
        suffix = " suppressed" if event.get("suppressed") else ""
        print(f"[{idx}] {event_type}", flush=True)
        if content:
            print(
                _format_user_response_pair(
                    user_id=user_id,
                    user_text=user_text,
                    bot_id=bot_id,
                    bot_text=content,
                ),
                flush=True,
            )
        else:
            print("<empty>", flush=True)
        if suffix:
            print(f"    {suffix.strip()}", flush=True)


def _capture_messages_lines(
    entries: list[dict[str, Any]],
    *,
    bot_id: int | None,
    user_id: int,
    user_text: str,
) -> list[str]:
    lines: list[str] = []
    if not entries:
        lines.append("<no messages>")
        return lines
    for idx, entry in enumerate(entries, start=1):
        content = str(entry.get("content") or "")
        suffix = " [suppressed]" if entry.get("suppressed") else ""
        lines.append(f"[{idx}]")
        lines.append(
            _format_user_response_pair(
                user_id=user_id,
                user_text=user_text,
                bot_id=bot_id,
                bot_text=content,
            )
        )
        if suffix:
            lines.append(f"    {suffix.strip()}")
        embed = entry.get("embed")
        if embed:
            lines.append(f"    embed.title={embed.get('title', '')!r}")
            lines.append(f"    embed.description={embed.get('description', '')!r}")
    return lines


def _capture_events_lines(
    events: list[dict[str, Any]],
    *,
    bot_id: int | None,
    user_id: int,
    user_text: str,
) -> list[str]:
    lines: list[str] = []
    if not events:
        lines.append("<no events>")
        return lines
    for idx, event in enumerate(events, start=1):
        event_type = str(event.get("type") or "event")
        content = str(event.get("content") or "")
        suffix = " suppressed" if event.get("suppressed") else ""
        lines.append(f"[{idx}] {event_type}")
        if content:
            lines.append(
                _format_user_response_pair(
                    user_id=user_id,
                    user_text=user_text,
                    bot_id=bot_id,
                    bot_text=content,
                )
            )
        else:
            lines.append("<empty>")
        if suffix:
            lines.append(f"    {suffix.strip()}")
    return lines


def _context_trace_lines(trace: Any) -> list[str]:
    if not isinstance(trace, dict) or not trace:
        return []
    lines: list[str] = ["=== retrieval trace ==="]
    mode = str(trace.get("mode") or "").strip()
    if mode:
        lines.append(f"mode={mode}")
    for key in ("guild_id", "channel_id", "user_id"):
        if key in trace:
            lines.append(f"{key}={trace.get(key)}")
    text = str(trace.get("text") or trace.get("query") or "").strip()
    if text:
        lines.append(f"text={text!r}")
    profile = str(trace.get("profile_summary") or trace.get("profile") or "").strip()
    if profile:
        lines.append("=== profile ===")
        lines.extend(profile.splitlines())
    answer = str(trace.get("answer") or "").strip()
    if answer:
        lines.append("=== answer ===")
        lines.extend(answer.splitlines())
    web_queries = trace.get("web_queries")
    if isinstance(web_queries, list) and web_queries:
        lines.append("=== web queries ===")
        for item in web_queries:
            if str(item or "").strip():
                lines.append(f"- {item}")
    details = trace.get("details")
    if isinstance(details, list) and details:
        lines.append("=== details ===")
        for item in details:
            if str(item or "").strip():
                lines.append(f"- {item}")
    blocks = trace.get("blocks")
    if isinstance(blocks, list) and blocks:
        lines.append("=== context blocks ===")
        for idx, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                continue
            title = str(block.get("title") or f"block {idx}").strip()
            body = str(block.get("body") or "").strip()
            lines.append(f"[{idx}] {title}")
            if body:
                lines.extend(body.splitlines())
    refs = trace.get("references")
    if isinstance(refs, list) and refs:
        lines.append("=== references ===")
        for item in refs:
            if str(item or "").strip():
                lines.append(f"- {item}")
    return lines


def _write_text_result(path_text: str, lines: list[str]) -> None:
    path = Path(str(path_text or "").strip()).expanduser()
    if not path.name:
        return
    if not path.is_absolute():
        path = ROOT_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _preview_mention_route(text: str, cog: Any) -> str:
    lowered = text.lower()
    if cog._is_runtime_model_query(text):
        return "runtime_model"
    if cog._is_capability_query(text):
        return "capability"
    if cog._is_channel_profile_query(text):
        return "channel_profile"
    if cog._is_local_activity_query(text):
        return "local_activity"
    if cog._is_person_lookup_query(text):
        return "person_lookup"
    if message_logger_module.is_current_info_intent(text) or message_logger_module.is_search_intent(text):
        return "web_search"
    if cog._is_bot_capability_or_game_query(text):
        return "capability_grounded_chat"
    if any(w in lowered for w in ("議事録開始", "議事録スタート", "minutes start", "start minutes")):
        return "minutes_start"
    if any(w in lowered for w in ("議事録停止", "議事録終了", "minutes stop", "stop minutes")):
        return "minutes_stop"
    return "chat"


def _summarize_llm_trace(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for call in calls:
        method = str(call.get("method") or "")
        item: dict[str, Any] = {"method": method}
        if "model" in call:
            item["model"] = call["model"]
        if method == "chat_simple":
            item["format"] = call.get("format")
            item["prompt"] = _truncate_text(call.get("prompt"), 300)
        elif method == "chat":
            item["tools"] = call.get("tools", [])
            messages = call.get("messages") or []
            item["messages"] = [
                {
                    "role": msg.get("role"),
                    "content": _truncate_text(msg.get("content"), 220),
                }
                for msg in messages
            ]
        elif method in {"web_search", "web_fetch", "embed"}:
            for key in ("query", "url", "text"):
                if key in call:
                    item[key] = _truncate_text(call.get(key), 240)
        else:
            for key, value in call.items():
                if key not in item:
                    item[key] = _truncate_text(value, 120)
        summary.append(item)
    return summary


async def _shutdown_preview_tasks() -> None:
    current = asyncio.current_task()
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _mock_recent_context(self: MessageStore, lines: int = 5) -> str:
    items = [
        "[12:00] testuser (1190939100514103357): こんにちは",
        "[12:01] testuser (1190939100514103357): 最近の投稿の例です",
        "[12:02] bot (387651883847909376): それは面白いですね",
    ]
    return "\n".join(items[-max(1, lines):])


def _mock_recent_messages(self: MessageStore, lines: int = 5, *, author_id: int | None = None) -> list[dict[str, Any]]:
    items = [
        {
            "id": 1001,
            "author_id": 1190939100514103357,
            "author": "testuser",
            "content": "こんにちは",
            "timestamp": "2026-04-20T12:00:00+09:00",
        },
        {
            "id": 1002,
            "author_id": 1190939100514103357,
            "author": "testuser",
            "content": "最近の投稿の例です",
            "timestamp": "2026-04-20T12:01:00+09:00",
        },
        {
            "id": 1003,
            "author_id": 387651883847909376,
            "author": "bot",
            "content": "それは面白いですね",
            "timestamp": "2026-04-20T12:02:00+09:00",
        },
    ]
    if author_id is not None:
        items = [item for item in items if int(item.get("author_id", 0) or 0) == int(author_id)]
    return items[-max(1, lines):]


def _mock_format_messages(self: MessageStore, messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for msg in messages:
        lines.append(
            f"[12:00] {msg.get('author', 'Unknown')} ({msg.get('author_id', 0)}): {msg.get('content', '')}"
        )
    return "\n".join(lines)


class MockOllamaResponse:
    def __init__(self, *, content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls or [],
        )


class MockOllamaClient:
    def __init__(self, *, seed_text: str = "") -> None:
        self.seed_text = seed_text
        self.calls: list[dict[str, Any]] = []
        self.client = SimpleNamespace(
            list=lambda: {
                "models": [
                    {"model": "mock-chat"},
                    {"model": "mock-summary"},
                    {"model": "mock-default"},
                ]
            }
        )

    def has_web_tools(self) -> bool:
        return True

    def has_embed(self) -> bool:
        return False

    def _last_user_text(self, messages: list[dict[str, Any]]) -> str:
        for item in reversed(messages):
            if str(item.get("role") or "").lower() == "user":
                return str(item.get("content") or "")
        return self.seed_text

    def _last_tool_text(self, messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for item in messages:
            if str(item.get("role") or "").lower() != "tool":
                continue
            tool_name = str(item.get("tool_name") or "tool")
            content = str(item.get("content") or "").strip()
            if content:
                parts.append(f"[{tool_name}]\n{content}")
        return "\n\n".join(parts)

    def _build_retrieval_plan(self, prompt: str) -> str:
        source_text = f"{prompt}\n{self.seed_text}".lower()
        plan: list[dict[str, Any]] = []

        def add(source: str, **kwargs: Any) -> None:
            item = {"source": source}
            item.update(kwargs)
            if item not in plan:
                plan.append(item)

        if any(keyword in source_text for keyword in ("このサーバー", "この場所", "このチャンネル", "ワールド")):
            add("channel_profile")
        if any(keyword in source_text for keyword in ("どんな人", "最後の投稿", "最後の発言", "生きてる", "プロフィール")):
            add("member_profile")
            add("member_history")
        if any(keyword in source_text for keyword in ("私", "自分", "俺", "僕", "最近の私", "私の情報")):
            add("member_profile")
            add("recent_user_history")
        if any(keyword in source_text for keyword in ("機能", "コマンド", "できること", "ゲーム")):
            add("local_knowledge")
            add("bot_command_catalog")
            add("bot_game_catalog")
        if any(keyword in source_text for keyword in ("ニュース", "速報", "今日", "最近", "最新", "事件")):
            add("web_search", scope="news" if any(k in source_text for k in ("ニュース", "速報", "今日")) else "web")
        if any(keyword in source_text for keyword in ("モデル", "ollama", "gemini")):
            add("runtime_model")
        if not plan:
            add("recent_turns")
        return json.dumps({"plan": plan}, ensure_ascii=False)

    def _extract_channel_profile_block(self, prompt: str) -> str:
        marker = "[プロフィール]"
        if marker not in prompt:
            return ""
        tail = prompt.split(marker, 1)[1].strip()
        if not tail:
            return ""
        lines = [line.rstrip() for line in tail.splitlines()]
        start_index = 0
        for idx, line in enumerate(lines):
            normalized = line.strip()
            if normalized.startswith("[") and " / " in normalized:
                start_index = idx
                break
        cleaned: list[str] = []
        for line in lines[start_index:]:
            if line.strip().startswith("[質問]"):
                break
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def _clean_preview_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"（モック応答）", "", cleaned)
        cleaned = re.sub(r"モック応答[:：\s]*", "", cleaned)
        lines: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue
            line = re.sub(r"^\[RAG:[^\]]+\]\s*", "", line)
            line = re.sub(r"\[RAG:[^\]]+\]", "", line)
            line = re.sub(r"^.*?を優先して返しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を優先しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を案内しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を要約しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を見て判断しました[。．.]*\s*", "", line)
            lines.append(line)
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _sentence(self, text: str) -> str:
        text = self._clean_preview_text(text)
        if not text:
            return "不明です。"
        if text[-1] not in "。．.!?！？":
            text += "。"
        return text

    def _build_channel_profile_answer(self, prompt: str) -> str:
        block = self._extract_channel_profile_block(prompt)
        if not block:
            return "この場所の情報はまだ十分にまとまっていません。"

        lines = [line.strip() for line in block.splitlines() if line.strip()]
        title = ""
        lead: str | None = None
        bullets: list[str] = []
        for line in lines:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                continue
            if line.startswith("-"):
                bullets.append(line.lstrip("-").strip())
            if len(bullets) >= 3:
                break

        if not title:
            body_lines = [line for line in lines if not line.startswith("[")]
            lead = next((line[:120] for line in body_lines if not line.startswith("-")), None)
        else:
            body_lines = [line for line in lines if not line.startswith("[")]
        summary = bullets[:2] if bullets else [line[:120] for line in body_lines[1:4] if line and not line.startswith("-")]

        answer_lines: list[str] = []
        if title:
            answer_lines.append(f"ここは、{title}です。")
        elif lead:
            match = re.match(r"^(?P<name>.+?)とは[、,：:\s]*(?P<rest>.*)$", lead)
            if match:
                place_name = match.group("name").strip()
                if place_name:
                    answer_lines.append(f"ここは、{place_name}の案内チャンネルです。")
            else:
                answer_lines.append(self._sentence(lead))
        if summary:
            combined = "、".join(item.rstrip("。．.") for item in summary if item)
            if combined:
                if any(keyword in combined for keyword in ("イベント", "ツアー", "ワールド", "観光", "参加")):
                    answer_lines.append("イベントの説明や参加方法の共有が行われます。")
                elif any(keyword in combined for keyword in ("Bot", "機能", "コマンド")):
                    answer_lines.append("Bot の使い方や機能の案内もあります。")
                else:
                    answer_lines.append(self._sentence(combined))
        if not answer_lines:
            answer_lines.append("この場所の情報はまだ十分にまとまっていません。")
        return "\n".join(answer_lines[:3])

    def _build_tool_answer(self, text: str) -> str:
        text = self._clean_preview_text(text)
        if not text:
            return "不明です。"
        if "web検索結果" in text or "検索結果" in text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            summary = [self._sentence(line.lstrip("- ").strip()) for line in lines[:4] if line.strip()]
            if summary:
                return "\n".join(summary[:3])
            return "関連情報はありますが、要点をまとめきれませんでした。"
        return "不明です。"

    def _build_chat_answer(self, prompt: str) -> str:
        text = prompt.strip()
        if not text:
            return "不明です。"
        lowered = text.lower()
        if "[プロフィール]" in text and "この場所の正式プロフィール" in text:
            return self._build_channel_profile_answer(text)
        if any(keyword in lowered for keyword in ("今日のニュース", "ニュース", "速報", "事件")):
            return "最新の話題なら、要点をまとめて短く案内できます。"
        if any(keyword in lowered for keyword in ("どんな人", "最後の投稿", "最後の発言", "プロフィール")):
            return "その人の最近の様子は、投稿内容を手がかりに自然にまとめられます。"
        if any(keyword in lowered for keyword in ("このサーバー", "この場所", "このチャンネル", "ワールド")):
            return "この場所は、案内や共有を行うための場所です。"
        if any(keyword in lowered for keyword in ("機能", "コマンド", "できること")):
            return "この Bot は、案内、検索、ログ要約などの用途で使えます。"
        return "不明です。"

    def chat_simple(self, model: str, prompt: str, stream: bool = False, format: str | None = None) -> str:
        self.calls.append(
            {
                "method": "chat_simple",
                "model": model,
                "format": format,
                "prompt": prompt,
            }
        )
        if format == "json":
            return self._build_retrieval_plan(prompt)
        return self._build_chat_answer(prompt)

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        tools: list[object] | None = None,
    ) -> MockOllamaResponse:
        self.calls.append(
            {
                "method": "chat",
                "model": model,
                "messages": messages,
                "tools": [getattr(tool, "__name__", repr(tool)) for tool in (tools or [])],
            }
        )
        user_text = self._last_user_text(messages)
        tool_text = self._last_tool_text(messages)
        if tool_text:
            return MockOllamaResponse(content=self._build_tool_answer(tool_text))
        if tools:
            lowered = user_text.lower()
            if any(keyword in lowered for keyword in ("今日", "ニュース", "速報", "事件", "最新", "検索")):
                return MockOllamaResponse(
                    tool_calls=[
                        {
                            "function": {
                                "name": "web_search",
                                "arguments": {"query": user_text},
                            }
                        }
                    ]
                )
        return MockOllamaResponse(content=self._build_chat_answer(user_text))

    def web_search(self, query: str, max_results: int = 3) -> str:
        self.calls.append(
            {
                "method": "web_search",
                "query": query,
                "max_results": max_results,
            }
        )
        return (
            f"Mock web search results for: {query}\n"
            "- https://example.com/news-1\n"
            "- https://example.com/news-2\n"
        )

    def web_fetch(self, url: str) -> str:
        self.calls.append(
            {
                "method": "web_fetch",
                "url": url,
            }
        )
        return f"Mock web fetch for {url}\nContent: This is a mocked article body."

    def embed(self, model: str, text: str) -> list[list[float]]:
        self.calls.append(
            {
                "method": "embed",
                "model": model,
                "text": text,
            }
        )
        return [[0.0, 0.1, 0.2]]


class MockAISearchService:
    def __init__(self, *, seed_text: str = "") -> None:
        self.seed_text = seed_text
        self.searcher = SimpleNamespace(config=SimpleNamespace(top_n=3))

    async def answer_ai_async(self, question: str, *, mode: str = "normal", news_only: bool | None = None):
        from src.kennybot.ai.search import AISearchAnswer, WebItem

        text = question or self.seed_text or ""
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("今日", "ニュース", "速報", "事件", "最新")):
            items = [
                WebItem(
                    title="Mock News 1: 事件や速報の例",
                    url="https://example.com/mock-news-1",
                    snippet="これはローカル preview 用のモック記事です。",
                    date="2026-04-20",
                    source="news",
                ),
                WebItem(
                    title="Mock News 2: 続報の例",
                    url="https://example.com/mock-news-2",
                    snippet="検索結果の流れを確認するためのダミーです。",
                    date="2026-04-20",
                    source="news",
                ),
            ]
            answer = (
                "Web検索結果を取得しました。\n\n"
                "【要点】\n"
                "- モック記事1は速報系の例です。\n"
                "- モック記事2は続報系の例です。\n\n"
                "【見つかった記事】\n"
                "- Mock News 1: 事件や速報の例（2026-04-20） / これはローカル preview 用のモック記事です。\n"
                "  https://example.com/mock-news-1\n"
                "- Mock News 2: 続報の例（2026-04-20） / 検索結果の流れを確認するためのダミーです。\n"
                "  https://example.com/mock-news-2\n\n"
                "【参考】\n"
                "- https://example.com/mock-news-1\n"
                "- https://example.com/mock-news-2"
            )
            return AISearchAnswer(
                query=text,
                searched_queries=[text],
                items=items,
                summaries=[
                    "速報の流れを確認するモック要約です。",
                    "続報を確認するモック要約です。",
                ],
                answer=answer,
            )
        items = [
            WebItem(
                title="Mock Web 1: 一般検索の例",
                url="https://example.com/mock-web-1",
                snippet="一般検索の preview 用ダミー記事です。",
                date="2026-04-20",
                source="web",
            ),
            WebItem(
                title="Mock Web 2: 参考資料の例",
                url="https://example.com/mock-web-2",
                snippet="実運用の流れを再現するための資料です。",
                date="2026-04-20",
                source="web",
            ),
        ]
        answer = (
            "Web検索結果を取得しました。\n\n"
            "【要点】\n"
            "- 一般検索の preview 用モック結果です。\n"
            "- 参考 URL が 2 件出る流れを確認できます。\n\n"
            "【見つかった記事】\n"
            "- Mock Web 1: 一般検索の例（2026-04-20） / 一般検索の preview 用ダミー記事です。\n"
            "  https://example.com/mock-web-1\n"
            "- Mock Web 2: 参考資料の例（2026-04-20） / 実運用の流れを再現するための資料です。\n"
            "  https://example.com/mock-web-2\n\n"
            "【参考】\n"
            "- https://example.com/mock-web-1\n"
            "- https://example.com/mock-web-2"
        )
        return AISearchAnswer(
            query=text,
            searched_queries=[text],
            items=items,
            summaries=[
                "一般検索の流れを確認するモック要約です。",
                "参考 URL を 2 件出すためのモック要約です。",
            ],
            answer=answer,
        )


def _install_mock_llm(bot: discord.Client, *, seed_text: str) -> None:
    mock_client = MockOllamaClient(seed_text=seed_text)
    bot.ollama_client = mock_client  # type: ignore[assignment]
    bot.ollama_embed_client = mock_client  # type: ignore[assignment]
    bot.ai_search = MockAISearchService(seed_text=seed_text)  # type: ignore[assignment]
    MessageStore._load_messages = lambda self: []  # type: ignore[assignment]
    MessageStore._save_messages = lambda self, messages: None  # type: ignore[assignment]
    MessageStore.get_recent_context = _mock_recent_context  # type: ignore[assignment]
    MessageStore.get_recent_messages = _mock_recent_messages  # type: ignore[assignment]
    MessageStore.format_messages = _mock_format_messages  # type: ignore[assignment]


async def _run_channel_profile_preview(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {}
    if getattr(args, "input_json", ""):
        try:
            loaded = json.loads(args.input_json)
            if isinstance(loaded, dict):
                payload = loaded
        except Exception as e:
            raise SystemExit(f"Invalid --input-json: {e}") from e

    def _get(name: str, default: Any) -> Any:
        if name in payload and payload[name] is not None:
            return payload[name]
        return getattr(args, name, default)

    guild_id = int(_get("guild_id", 972052382315855912))
    guild_name = str(_get("guild_name", "debug-guild"))
    channel_id = int(_get("channel_id", 1493246078357606430))
    channel_name = str(_get("channel_name", "debug-channel"))
    scope = str(_get("scope", "auto"))
    question = str(_get("question", "このサーバーはなにするところ？"))
    limit = int(_get("limit", 6))
    max_chars = int(_get("max_chars", 2600))
    direct = bool(_get("direct", False))
    json_output = bool(_get("json", False))
    preview = build_channel_profile_preview(
        root=ROOT_DIR,
        guild_id=guild_id,
        channel_id=channel_id,
        scope=scope,
        question=question,
        limit=limit,
        max_chars=max_chars,
    )
    if not preview["profile"]:
        if json_output:
            print(json.dumps(preview, ensure_ascii=False))
        else:
            print("=== channel profile preview ===")
            print("profile=<empty>")
        return 0

    if direct:
        if json_output:
            output = dict(preview)
            output["answer"] = ""
            print(json.dumps(output, ensure_ascii=False))
        else:
            print("=== channel profile preview ===")
            print(f"guild_id={guild_id}")
            print(f"channel_id={channel_id}")
            print(preview["profile"])
        return 0

    if json_output:
        print(json.dumps(preview, ensure_ascii=False))
    else:
        print("=== channel profile preview ===")
        print(f"guild_id={guild_id}")
        print(f"channel_id={channel_id}")
        print("=== profile block ===")
        print(preview["profile"])
        print("=== answer preview ===")
        print(preview["answer"])
    return 0
async def _run_mention_preview(args: argparse.Namespace) -> int:
    bot = create_bot()
    print("=== debug checkpoint: before setup_hook ===", flush=True)
    await bot.setup_hook()
    print("=== debug checkpoint: after setup_hook ===", flush=True)
    send_policy = PreviewSendPolicy(deliver=not bool(getattr(args, "dry_run_send", False)))
    bot._connection.user = FakeMember(args.bot_user_id, args.bot_user_name, bot=True)
    if args.mock_llm:
        _install_mock_llm(bot, seed_text=args.text)
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

    guild = FakeGuild(
        args.guild_id,
        args.guild_name,
        members=[
            bot._connection.user,
            FakeMember(args.author_id, args.author_name, display_name=args.author_display_name),
        ],
    )
    guild.me = bot._connection.user
    capture_channel = CaptureChannel(args.channel_id, name=args.channel_name, send_policy=send_policy)
    capture_channel.guild = guild
    capture_channel.history_messages = [
        FakeMessage(
            message_id=9001,
            content="こんにちは",
            author=guild.get_member(args.author_id) or FakeMember(args.author_id, args.author_name, display_name=args.author_display_name),
            guild=guild,
            channel=capture_channel,
        ),
        FakeMessage(
            message_id=9002,
            content="このサーバーは何のやつ？",
            author=guild.get_member(args.author_id) or FakeMember(args.author_id, args.author_name, display_name=args.author_display_name),
            guild=guild,
            channel=capture_channel,
        ),
        FakeMessage(
            message_id=9003,
            content="VRC世界旅行の案内です",
            author=bot._connection.user,
            guild=guild,
            channel=capture_channel,
        ),
    ]
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
    mention_presets = {
        "chat": "こんにちは",
        "runtime_model": "モデル名は？",
        "capability": "このBotは何ができる？",
        "web_search": "今日のニュースは？",
        "current_info": "最近の京都の事件を教えて",
        "news": "今日のニュースは？",
        "search": "この事件について最新情報を教えて",
        "person": f"<@{args.mention_user_id or args.author_id}> はどんな人？",
        "person_history": f"<@{args.mention_user_id or args.author_id}> の最後の投稿ある？",
        "local_activity": f"<@{args.mention_user_id or args.author_id}> 最近の行動は？",
        "server": "このサーバーは何のやつ？",
        "channel_profile": "このチャンネルは何をする場所？",
        "minutes_start": "議事録開始",
        "minutes_stop": "議事録停止",
    }
    if args.preset and not args.text:
        args.text = mention_presets.get(args.preset, args.preset)

    msg = FakeMessage(
        message_id=args.message_id,
        content=args.text,
        author=author,
        guild=guild,
        channel=capture_channel,
        mentions=mentions,
    )
    text = args.text
    user_trace = _format_user_response_pair(
        user_id=author.id,
        user_text=text,
        bot_id=bot._connection.user.id if bot._connection.user else None,
    )
    cog = bot.get_cog("MessageLogger")
    if cog is None:
        raise RuntimeError("MessageLogger cog not available")
    cog._schedule_message_index = lambda *a, **k: None  # type: ignore[assignment]
    if args.mock_llm:
        async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def _noop_ticket(*_args: Any, **_kwargs: Any) -> object:
            return SimpleNamespace(id=0)

        bot.ai_progress_tracker.create_ticket = _noop_ticket  # type: ignore[assignment]
        bot.ai_progress_tracker.acquire = _noop_async  # type: ignore[assignment]
        bot.ai_progress_tracker.release = _noop_async  # type: ignore[assignment]
        bot.ai_progress_tracker.render = lambda ticket, elapsed, model: f"{model}が推論中..{elapsed}秒"  # type: ignore[assignment]
        cog._ai_progress_countdowns.start_countup = _noop_async  # type: ignore[assignment]
        cog._ai_retry_countdowns.start_or_replace = _noop_async  # type: ignore[assignment]
    if args.no_ai:
        text = args.text
        route = _preview_mention_route(text, cog)
        result_lines = [
            "=== mention routing preview ===",
            user_trace,
            f"route={route}",
            f"text={text!r}",
            f"mentions={[m.id for m in mentions]!r}",
        ]
        print("=== mention routing preview ===")
        print(user_trace)
        print(f"route={route}")
        print(f"text={text!r}")
        print(f"mentions={[m.id for m in mentions]!r}")
        if str(getattr(args, "result_file", "") or "").strip():
            _write_text_result(str(args.result_file), result_lines)
        return 0

    if args.mock_llm and args.dry_run_send and args.preset in {"channel_profile", "runtime_model", "capability"}:
        route_name = _preview_mention_route(args.text, cog)
        result_lines: list[str] = []
        if route_name == "runtime_model":
            await cog._send_runtime_model_reply(
                capture_channel,
                mention=msg.author.mention,
                source_msg=msg,
                input_text=args.text,
            )
        elif route_name == "capability":
            await cog._answer_capability_query(
                capture_channel,
                args.text,
                mention=msg.author.mention,
                source_msg=msg,
                channel_id=capture_channel.id,
            )
        else:
            await cog._answer_channel_profile_query(
                capture_channel,
                args.text,
                mention=msg.author.mention,
                source_msg=msg,
                channel_id=capture_channel.id,
            )
        result_lines.extend(
            [
                "=== mention mock preview ===",
                user_trace,
                f"route={route_name}",
                f"text={args.text!r}",
                f"mentions={[m.id for m in mentions]!r}",
                "=== captured messages ===",
            ]
        )
        print("=== mention mock preview ===", flush=True)
        print(user_trace, flush=True)
        print(f"route={route_name}", flush=True)
        print(f"text={args.text!r}", flush=True)
        print(f"mentions={[m.id for m in mentions]!r}", flush=True)
        print("=== captured messages ===", flush=True)
        message_lines = _capture_messages_lines(
            capture_channel.messages,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(message_lines)
        for line in message_lines:
            print(line, flush=True)
        print("=== message events ===", flush=True)
        result_lines.append("=== message events ===")
        event_lines = _capture_events_lines(
            capture_channel.events,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(event_lines)
        _print_capture_events(
            capture_channel.events,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(_context_trace_lines(getattr(cog, "_last_context_trace", {})))
        if str(getattr(args, "result_file", "") or "").strip():
            _write_text_result(str(args.result_file), result_lines)
        sys.stdout.flush()
        os._exit(0)

    if args.mock_llm and args.preset in {"person", "person_history", "local_activity"}:
        route_name = "person_lookup" if args.preset == "person" else args.preset
        mock_client = bot.ollama_client
        answer = mock_client.chat_simple(model="mock-chat", prompt=args.text or args.preset)
        if not answer:
            answer = "不明です。"
        await capture_channel.send(f"{msg.author.mention}\n{answer}")
        result_lines = [
            "=== mention mock preview ===",
            user_trace,
            f"route={route_name}",
            f"text={args.text!r}",
            f"mentions={[m.id for m in mentions]!r}",
            "=== captured messages ===",
        ]
        print("=== mention mock preview ===")
        print(user_trace)
        print(f"route={route_name}")
        print(f"text={args.text!r}")
        print(f"mentions={[m.id for m in mentions]!r}")
        print("=== captured messages ===")
        message_lines = _capture_messages_lines(
            capture_channel.messages,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(message_lines)
        for line in message_lines:
            print(line)
        if args.trace_llm:
            print("=== llm trace ===", flush=True)
            print(json.dumps(getattr(bot.ollama_client, "calls", []), ensure_ascii=False, indent=2), flush=True)
            result_lines.append("=== llm trace ===")
            result_lines.extend(
                json.dumps(getattr(bot.ollama_client, "calls", []), ensure_ascii=False, indent=2).splitlines()
            )
        result_file_arg = str(getattr(args, "result_file", "") or "").strip()
        if result_file_arg:
            _write_text_result(result_file_arg, result_lines)
        trace_file_arg = str(getattr(args, "trace_file", "") or "").strip()
        if trace_file_arg:
            _write_text_result(trace_file_arg, result_lines)
        sys.stdout.flush()
        os._exit(0)

    if args.mock_llm and args.dry_run_send and args.preset in {"web_search", "news", "search", "current_info"}:
        text = msg.content
        user_display = author.display_name or author.name or str(author.id)
        print("=== mention mock preview ===", flush=True)
        print(user_trace, flush=True)
        print("route=web_search", flush=True)
        print(f"text={text!r}", flush=True)
        print(f"mentions={[m.id for m in mentions]!r}", flush=True)
        print("=== retrieval context ===", flush=True)
        web_scope = "news" if args.preset in {"news", "current_info"} else "web"
        context_body, planned_refs, _title_map, web_queries = await cog._build_current_info_context_once(
            text,
            web_scope=web_scope,
        )
        print(context_body or "<empty>", flush=True)
        planned_details = [
            f"web_scope={web_scope}",
            f"web_search query={text}",
        ]
        print("=== planned refs ===", flush=True)
        print(json.dumps(planned_refs, ensure_ascii=False, indent=2), flush=True)
        print("=== planned details ===", flush=True)
        print(json.dumps(planned_details, ensure_ascii=False, indent=2), flush=True)
        cog._last_context_trace = {
            "mode": "web_search",
            "guild_id": getattr(msg.guild, "id", None),
            "channel_id": getattr(msg.channel, "id", None),
            "query": text,
            "answer": context_body or "",
            "references": planned_refs,
            "web_queries": web_queries,
            "details": planned_details,
        }
        absolute_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        absolute_datetime = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S JST")
        prompt = message_logger_module.PROMPT_TEMPLATE.format(
            user_display=user_display,
            history_context=context_body or "<empty>",
            user_message=text,
            max_response_length_prompt=cog._cfg_int("chat.max_response_length_prompt", 500),
        )
        chat_messages = [
            {
                "role": "system",
                "content": message_logger_module.get_prompt("chat", "system_message").format(
                    absolute_date=absolute_date,
                    absolute_datetime=absolute_datetime,
                    channel_profile_block="",
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]
        response = bot.ollama_client.chat(
            model=cog._current_chat_model_name(),
            messages=chat_messages,
            stream=False,
            tools=[],
        )
        if isinstance(response, dict):
            message = response.get("message", {})
            if isinstance(message, dict):
                answer = str(message.get("content") or "")
            else:
                answer = str(getattr(message, "content", "") or "")
        else:
            message = getattr(response, "message", None)
            if isinstance(message, dict):
                answer = str(message.get("content") or "")
            elif message is not None:
                answer = str(getattr(message, "content", "") or "")
            else:
                answer = str(getattr(response, "content", "") or "")
        answer = cog._sanitize_user_visible_answer((answer or "").strip())
        if not answer:
            answer = cog._sanitize_user_visible_answer(context_body or "")
        if not answer:
            answer = "最新の話題はまだ見つかりませんでした。"
        await capture_channel.send(
            f"{msg.author.mention}\n{answer}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        print("=== captured messages ===", flush=True)
        message_lines = _capture_messages_lines(
            capture_channel.messages,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines = [
            "=== mention mock preview ===",
            user_trace,
            "route=web_search",
            f"text={text!r}",
            f"mentions={[m.id for m in mentions]!r}",
            "=== retrieval context ===",
            context_body or "<empty>",
            "=== planned refs ===",
            json.dumps(planned_refs, ensure_ascii=False, indent=2),
            "=== planned details ===",
            json.dumps(planned_details, ensure_ascii=False, indent=2),
            "=== captured messages ===",
            *message_lines,
        ]
        for line in message_lines:
            print(line, flush=True)
        print("=== message events ===", flush=True)
        event_lines = _capture_events_lines(
            capture_channel.events,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.append("=== message events ===")
        result_lines.extend(event_lines)
        _print_capture_events(
            capture_channel.events,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(_context_trace_lines(getattr(cog, "_last_context_trace", {})))
        result_lines.append("=== tool refs ===")
        result_lines.append(json.dumps([], ensure_ascii=False, indent=2))
        result_lines.append("=== tool queries ===")
        result_lines.append(json.dumps(web_queries, ensure_ascii=False, indent=2))
        result_lines.append("=== tool details ===")
        result_lines.append(json.dumps(planned_details, ensure_ascii=False, indent=2))
        if args.trace_llm:
            print("=== llm trace ===", flush=True)
            trace_payload = json.dumps(getattr(bot.ollama_client, "calls", []), ensure_ascii=False, indent=2)
            print(trace_payload, flush=True)
            result_lines.append("=== llm trace ===")
            result_lines.extend(trace_payload.splitlines())
        result_file_arg = str(getattr(args, "result_file", "") or "").strip()
        if result_file_arg:
            _write_text_result(result_file_arg, result_lines)
        trace_file_arg = str(getattr(args, "trace_file", "") or "").strip()
        if trace_file_arg:
            _write_text_result(trace_file_arg, result_lines)
        sys.stdout.flush()
        os._exit(0)

    if args.mock_llm and args.dry_run_send and args.preset == "chat":
        text = msg.content
        user_display = author.display_name or author.name or str(author.id)
        result_lines = [
            "=== mention mock preview ===",
            user_trace,
            "route=chat",
            f"text={text!r}",
            f"mentions={[m.id for m in mentions]!r}",
        ]
        print("=== mention mock preview ===", flush=True)
        print(user_trace, flush=True)
        print("route=chat", flush=True)
        print(f"text={text!r}", flush=True)
        print(f"mentions={[m.id for m in mentions]!r}", flush=True)
        print("=== debug trace: direct prompt flow ===", flush=True)
        history_context, planned_refs, web_queries, planned_details = await cog._resolve_chat_context(
            msg=msg,
            user_display=user_display,
            text=text,
        )
        print("=== retrieval context ===", flush=True)
        print(history_context or "<empty>", flush=True)
        print("=== planned refs ===", flush=True)
        print(json.dumps(planned_refs, ensure_ascii=False, indent=2), flush=True)
        print("=== planned details ===", flush=True)
        print(json.dumps(planned_details, ensure_ascii=False, indent=2), flush=True)
        absolute_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        absolute_datetime = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S JST")
        prompt = message_logger_module.PROMPT_TEMPLATE.format(
            user_display=user_display,
            history_context=history_context,
            user_message=text,
            max_response_length_prompt=cog._cfg_int("chat.max_response_length_prompt", 500),
        )
        chat_messages = [
            {
                "role": "system",
                "content": message_logger_module.get_prompt("chat", "system_message").format(
                    absolute_date=absolute_date,
                    absolute_datetime=absolute_datetime,
                    channel_profile_block="",
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]
        response = bot.ollama_client.chat(
            model=cog._current_chat_model_name(),
            messages=chat_messages,
            stream=False,
            tools=[],
        )
        if isinstance(response, dict):
            message = response.get("message", {})
            if isinstance(message, dict):
                answer = str(message.get("content") or "")
            else:
                answer = str(getattr(message, "content", "") or "")
        else:
            message = getattr(response, "message", None)
            if isinstance(message, dict):
                answer = str(message.get("content") or "")
            elif message is not None:
                answer = str(getattr(message, "content", "") or "")
            else:
                answer = str(getattr(response, "content", "") or "")
        answer = cog._sanitize_user_visible_answer((answer or "").strip())
        await capture_channel.send(
            f"{msg.author.mention}\n{answer}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        print("=== captured messages ===", flush=True)
        message_lines = _capture_messages_lines(
            capture_channel.messages,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(message_lines)
        for line in message_lines:
            print(line, flush=True)
        print("=== message events ===", flush=True)
        result_lines.append("=== message events ===")
        event_lines = _capture_events_lines(
            capture_channel.events,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        result_lines.extend(event_lines)
        _print_capture_events(
            capture_channel.events,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=args.text,
        )
        if str(getattr(args, "result_file", "") or "").strip():
            _write_text_result(str(args.result_file), result_lines)
        sys.stdout.flush()
        os._exit(0)

    if args.mock_llm and args.trace_llm:
        trace_lines: list[str] = []
        result_file_arg = str(getattr(args, "result_file", "") or "").strip()
        trace_file_arg = str(getattr(args, "trace_file", "") or "").strip()
        if trace_file_arg:
            trace_path = Path(trace_file_arg).expanduser()
            if not trace_path.is_absolute():
                trace_path = ROOT_DIR / trace_path
        else:
            trace_path = ROOT_DIR / DEBUG_ROUTE_HISTORY_DIR / "debug_route_trace.txt"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("", encoding="utf-8")

        def emit(line: str = "") -> None:
            trace_lines.append(line)
            print(line)
            with trace_path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")

        def emit_json(header: str, value: Any) -> None:
            emit(header)
            payload = json.dumps(value, ensure_ascii=False, indent=2)
            trace_lines.extend(payload.splitlines())
            print(payload)

        text = msg.content
        user_display = author.display_name or author.name or str(author.id)
        emit("=== user ===")
        emit(f"【ユーザー】<@{author.id}> {text}")
        emit("=== debug trace: direct prompt flow ===")
        history_context, planned_refs, web_queries, planned_details = await cog._resolve_chat_context(
            msg=msg,
            user_display=user_display,
            text=text,
        )
        emit("=== retrieval context ===")
        emit(history_context or "<empty>")
        emit_json("=== planned refs ===", planned_refs)
        emit_json("=== planned details ===", planned_details)
        trace_lines.extend(_context_trace_lines(getattr(cog, "_last_context_trace", {})))
        absolute_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        absolute_datetime = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S JST")
        prompt = message_logger_module.PROMPT_TEMPLATE.format(
            user_display=user_display,
            history_context=history_context,
            user_message=text,
            max_response_length_prompt=cog._cfg_int("chat.max_response_length_prompt", 500),
        )
        chat_messages = [
            {
                "role": "system",
                "content": message_logger_module.get_prompt("chat", "system_message").format(
                    absolute_date=absolute_date,
                    absolute_datetime=absolute_datetime,
                    channel_profile_block="",
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]
        emit("=== final prompt ===")
        emit(prompt)
        emit_json("=== chat messages ===", chat_messages)
        tools = [
            cog._get_local_knowledge,
            cog._get_bot_game_catalog,
            cog._get_bot_command_catalog,
            cog._get_runtime_model_info,
            cog._search_vrchat_world,
            bot.ollama_client.web_search,
            bot.ollama_client.web_fetch,
        ]
        emit("=== debug checkpoint: before trace tool loop ===")
        emit_json(
            "=== llm request payload ===",
            _summarize_llm_trace(
                [
                    {
                        "method": "chat",
                        "model": cog._current_chat_model_name(),
                        "messages": chat_messages,
                        "tools": [],
                    }
                ]
            )[0],
        )
        response = bot.ollama_client.chat(
            model=cog._current_chat_model_name(),
            messages=chat_messages,
            stream=False,
            tools=[],
        )
        if isinstance(response, dict):
            message = response.get("message", {})
            if isinstance(message, dict):
                answer = str(message.get("content") or "")
            else:
                answer = str(getattr(message, "content", "") or "")
        else:
            message = getattr(response, "message", None)
            if isinstance(message, dict):
                answer = str(message.get("content") or "")
            elif message is not None:
                answer = str(getattr(message, "content", "") or "")
            else:
                answer = str(getattr(response, "content", "") or "")
        tool_references = []
        tool_queries = []
        tool_reference_details = []
        emit("=== debug checkpoint: after responder call ===")
        answer = cog._sanitize_user_visible_answer((answer or "").strip())
        emit("=== answer ===")
        emit(answer or "<empty>")
        emit("=== user / response ===")
        emit(_format_user_response_pair(
            user_id=author.id,
            user_text=text,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            bot_text=answer or "<empty>",
        ))
        emit("=== message events ===")
        for idx, event in enumerate(capture_channel.events, start=1):
            emit(f"[{idx}] {event.get('type')}")
            emit(_format_user_response_pair(
                user_id=author.id,
                user_text=text,
                bot_id=bot._connection.user.id if bot._connection.user else None,
                bot_text=str(event.get("content") or "<empty>"),
            ))
        emit_json("=== tool refs ===", list(tool_references))
        emit_json("=== tool queries ===", list(tool_queries))
        emit_json("=== tool details ===", list(tool_reference_details))
        emit_json("=== llm trace ===", _summarize_llm_trace(getattr(bot.ollama_client, "calls", [])))
        if result_file_arg:
            _write_text_result(result_file_arg, trace_lines)
        if trace_path:
            print(f"=== trace file written: {trace_path} ===")
        if args.dry_run_send:
            await _shutdown_preview_tasks()
            os._exit(0)
        return 0

    print("=== debug checkpoint: before on_message ===", flush=True)
    await cog.on_message(msg)
    print("=== debug checkpoint: after on_message ===", flush=True)

    print("=== captured messages ===")
    result_lines = [
        "=== captured messages ===",
        *_capture_messages_lines(
            capture_channel.messages,
            bot_id=bot._connection.user.id if bot._connection.user else None,
            user_id=author.id,
            user_text=text,
        ),
    ]
    message_lines = result_lines[1:]
    for line in message_lines:
        print(line)
    print("=== message events ===")
    event_lines = _capture_events_lines(
        capture_channel.events,
        bot_id=bot._connection.user.id if bot._connection.user else None,
        user_id=author.id,
        user_text=text,
    )
    result_lines.append("=== message events ===")
    result_lines.extend(event_lines)
    _print_capture_events(
        capture_channel.events,
        bot_id=bot._connection.user.id if bot._connection.user else None,
        user_id=author.id,
        user_text=text,
    )
    result_lines.extend(_context_trace_lines(getattr(cog, "_last_context_trace", {})))
    if args.trace_llm and args.mock_llm:
        print("=== llm trace ===")
        print(
            json.dumps(
                _summarize_llm_trace(getattr(bot.ollama_client, "calls", [])),
                ensure_ascii=False,
                indent=2,
            )
        )
        result_lines.append("=== llm trace ===")
        result_lines.extend(
            json.dumps(
                _summarize_llm_trace(getattr(bot.ollama_client, "calls", [])),
                ensure_ascii=False,
                indent=2,
            ).splitlines()
        )
    trace_file_arg = str(getattr(args, "trace_file", "") or "").strip()
    if trace_file_arg:
        _write_text_result(trace_file_arg, result_lines)
    if str(getattr(args, "result_file", "") or "").strip():
        _write_text_result(str(args.result_file), result_lines)
    if args.mock_llm and args.dry_run_send:
        await _shutdown_preview_tasks()
        os._exit(0)
    return 0


async def _run_slash_preview(args: argparse.Namespace) -> int:
    bot = create_bot()
    await bot.setup_hook()
    send_policy = PreviewSendPolicy(deliver=not bool(getattr(args, "dry_run_send", False)))
    bot._connection.user = FakeMember(args.bot_user_id, args.bot_user_name, bot=True)
    if args.mock_llm:
        _install_mock_llm(bot, seed_text=args.command)

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

    capture_channel = CaptureChannel(args.channel_id, name=args.channel_name, send_policy=send_policy)
    guild = FakeGuild(
        args.guild_id,
        args.guild_name,
        members=[bot._connection.user, FakeMember(args.author_id, args.author_name, display_name=args.author_display_name)],
    )
    guild.me = bot._connection.user
    guild.text_channels = [capture_channel]
    capture_channel.guild = guild
    user = guild.get_member(args.author_id)
    assert user is not None
    interaction = FakeInteraction(bot=bot, guild=guild, channel=capture_channel, user=user, send_policy=send_policy)
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
    response_lines = ["=== slash response events ==="]
    for idx, event in enumerate(interaction.response_events, start=1):
        line = f"[{idx}] {event}"
        print(line)
        response_lines.append(line)
    print("=== captured channel messages ===")
    channel_lines = ["=== captured channel messages ===", *_capture_messages_lines(
        capture_channel.messages,
        bot_id=bot._connection.user.id if bot._connection.user else None,
        user_id=interaction.user.id,
        user_text=args.command,
    )]
    for line in channel_lines[1:]:
        print(line, flush=True)
    print("=== message events ===")
    event_lines = _capture_events_lines(
        capture_channel.events,
        bot_id=bot._connection.user.id if bot._connection.user else None,
        user_id=interaction.user.id,
        user_text=args.command,
    )
    for line in event_lines:
        print(line)
    result_file_arg = str(getattr(args, "result_file", "") or "").strip()
    if result_file_arg:
        _write_text_result(
            result_file_arg,
            response_lines
            + channel_lines
            + ["=== message events ==="]
            + event_lines
            + _context_trace_lines(getattr(getattr(interaction, "client", None), "_last_context_trace", {})),
        )
    trace_file_arg = str(getattr(args, "trace_file", "") or "").strip()
    if trace_file_arg:
        _write_text_result(
            trace_file_arg,
            response_lines
            + channel_lines
            + ["=== message events ==="]
            + event_lines
            + _context_trace_lines(getattr(getattr(interaction, "client", None), "_last_context_trace", {})),
        )
    if args.mock_llm and args.dry_run_send:
        sys.stdout.flush()
        os._exit(0)
    await _shutdown_preview_tasks()
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
    base.add_argument("--mock-llm", action="store_true", help="Use mocked AI/search backends for local preview")
    base.add_argument(
        "--dry-run-send",
        action="store_true",
        help="Run the full preview flow but suppress outbound send delivery",
    )
    base.add_argument(
        "--result-file",
        type=str,
        default="",
        help="Write the preview result to a text file (default: no file)",
    )
    base.add_argument(
        "--trace-file",
        type=str,
        default="",
        help="Write the preview trace to a text file (default: no file)",
    )

    p_mention = sub.add_parser("mention", parents=[base], help="Preview a mention/message response")
    p_mention.add_argument("text", nargs="?", default="", type=str)
    p_mention.add_argument("--message-id", type=int, default=1)
    p_mention.add_argument("--mention-user-id", type=int, default=0)
    p_mention.add_argument("--mention-user-name", type=str, default="")
    p_mention.add_argument("--mention-user-display-name", type=str, default="")
    p_mention.add_argument("--no-ai", action="store_true", help="Only print the routing decision")
    p_mention.add_argument("--trace-llm", action="store_true", help="Dump captured LLM calls as JSON")
    p_mention.add_argument(
        "--preset",
        type=str,
        default="",
        choices=[
            "",
            "chat",
            "runtime_model",
            "capability",
            "web_search",
            "current_info",
            "news",
            "search",
            "person",
            "person_history",
            "local_activity",
            "server",
            "channel_profile",
            "minutes_start",
            "minutes_stop",
        ],
        help="Use a built-in text preset when text is omitted",
    )

    p_profile = sub.add_parser("profile", parents=[base], help="Preview a channel/server profile lookup")
    p_profile.add_argument(
        "--question",
        type=str,
        default="このサーバーはなにするところ？",
        help="Question to ask against the local channel profile",
    )
    p_profile.add_argument("--message-id", type=int, default=1)
    p_profile.add_argument(
        "--scope",
        type=str,
        default="auto",
        choices=["auto", "guild", "channel", "legacy_channel"],
        help="Which scoped RAG to read",
    )
    p_profile.add_argument("--limit", type=int, default=6)
    p_profile.add_argument("--max-chars", type=int, default=2600)
    p_profile.add_argument("--direct", action="store_true", help="Print the profile block only, without LLM summarization")
    p_profile.add_argument("--input-json", type=str, default="", help="Override profile preview arguments from a JSON object")
    p_profile.add_argument("--json", action="store_true", help="Print a JSON response instead of human-readable text")

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
    if args.mode == "profile":
        return asyncio.run(_run_channel_profile_preview(args))
    if args.mode == "slash":
        return asyncio.run(_run_slash_preview(args))
    raise SystemExit(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
