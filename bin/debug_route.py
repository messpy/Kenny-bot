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
        return CaptureSentMessage(self, entry)

    def typing(self) -> _NoopAsyncContext:
        return _NoopAsyncContext()


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
        return self

    async def delete(self) -> None:
        return None


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


async def _noop_async_send_event_log(*args: Any, **kwargs: Any) -> None:
    return None


def _noop_add_message(self: MessageStore, *args: Any, **kwargs: Any) -> None:
    return None


def _noop_log(*args: Any, **kwargs: Any) -> None:
    return None


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

    def _build_tool_answer(self, text: str) -> str:
        text = text.strip()
        if not text:
            return "（モック応答）"
        if "web検索結果" in text or "検索結果" in text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "検索結果を確認しました。\n" + "\n".join(lines[:5])
        return f"（モック応答）{text[:300]}"

    def _build_chat_answer(self, prompt: str) -> str:
        text = prompt.strip()
        if not text:
            return "（モック応答）"
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("今日のニュース", "ニュース", "速報", "事件")):
            return "（モック応答）ニュース検索の結果を要約しました。"
        if any(keyword in lowered for keyword in ("どんな人", "最後の投稿", "最後の発言", "プロフィール")):
            return "（モック応答）プロフィールと最近の発言を見て判断しました。"
        if any(keyword in lowered for keyword in ("このサーバー", "この場所", "このチャンネル", "ワールド")):
            return "（モック応答）場所の説明を優先して返しました。"
        if any(keyword in lowered for keyword in ("機能", "コマンド", "できること")):
            return "（モック応答）Bot の機能を案内しました。"
        return f"（モック応答）{text[:300]}"

    def chat_simple(self, model: str, prompt: str, stream: bool = False, format: str | None = None) -> str:
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
        return (
            f"Mock web search results for: {query}\n"
            "- https://example.com/news-1\n"
            "- https://example.com/news-2\n"
        )

    def web_fetch(self, url: str) -> str:
        return f"Mock web fetch for {url}\nContent: This is a mocked article body."

    def embed(self, model: str, text: str) -> list[list[float]]:
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
async def _run_mention_preview(args: argparse.Namespace) -> int:
    bot = create_bot()
    await bot.setup_hook()
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
        lowered = text.lower()
        route = "chat"
        if cog._is_runtime_model_query(text):
            route = "runtime_model"
        elif cog._is_capability_query(text):
            route = "capability"
        elif cog._is_channel_profile_query(text):
            route = "channel_profile"
        elif cog._is_local_activity_query(text):
            route = "local_activity"
        elif cog._is_person_lookup_query(text):
            route = "person_lookup"
        elif message_logger_module.is_current_info_intent(text) or message_logger_module.is_search_intent(text):
            route = "web_search"
        elif cog._is_bot_capability_or_game_query(text):
            route = "capability_grounded_chat"
        elif any(w in lowered for w in ("議事録開始", "議事録スタート", "minutes start", "start minutes")):
            route = "minutes_start"
        elif any(w in lowered for w in ("議事録停止", "議事録終了", "minutes stop", "stop minutes")):
            route = "minutes_stop"
        print("=== mention routing preview ===")
        print(f"route={route}")
        print(f"text={text!r}")
        print(f"mentions={[m.id for m in mentions]!r}")
        return 0

    if args.mock_llm and args.preset in {"person", "person_history", "local_activity"}:
        route_name = "person_lookup" if args.preset == "person" else args.preset
        mock_client = bot.ollama_client
        answer = mock_client.chat_simple(model="mock-chat", prompt=args.text or args.preset)
        if not answer:
            answer = "（モック応答）プロフィールと最近の発言を見て判断しました。"
        await capture_channel.send(f"{msg.author.mention}\n{answer}")
        print("=== mention mock preview ===")
        print(f"route={route_name}")
        print(f"text={args.text!r}")
        print(f"mentions={[m.id for m in mentions]!r}")
        print("=== captured messages ===")
        for idx, entry in enumerate(capture_channel.messages, start=1):
            print(f"[{idx}] {entry.get('content')!r}")
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
    base.add_argument("--mock-llm", action="store_true", help="Use mocked AI/search backends for local preview")

    p_mention = sub.add_parser("mention", parents=[base], help="Preview a mention/message response")
    p_mention.add_argument("text", nargs="?", default="", type=str)
    p_mention.add_argument("--message-id", type=int, default=1)
    p_mention.add_argument("--mention-user-id", type=int, default=0)
    p_mention.add_argument("--mention-user-name", type=str, default="")
    p_mention.add_argument("--mention-user-display-name", type=str, default="")
    p_mention.add_argument("--no-ai", action="store_true", help="Only print the routing decision")
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
