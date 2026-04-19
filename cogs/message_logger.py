# cogs/message_logger.py
# 会話 + リアクション

import json
import io
import logging
import re
import subprocess
import time
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from utils.config import (
    PROMPT_TEMPLATE,
    HISTORY_CONTEXT_TEMPLATE,
)
from utils.message_store import MessageStore
from utils.live_info import ExternalContext, LiveInfoService
from utils.local_rag import LocalRAG
from utils.runtime_settings import get_settings
from utils.event_logger import send_event_log
from utils.countdown import ChannelCountdown
from utils.message_vector_store import MessageVectorStore
from utils.command_catalog import COMMAND_CATEGORY_ORDER, HELP_SECTIONS, SLASH_COMMANDS
from utils.paths import MESSAGE_VECTOR_DB_PATH
from utils.message_logger import log_user_message, log_ai_output, log_system_event
from cogs.base import BaseCog
from utils.channel import resolve_log_channel
from utils.text import (
    normalize_user_text,
    normalize_keyword_match_text,
    is_search_intent,
    is_current_info_intent,
    strip_ansi_and_ctrl,
)
from utils.prompts import get_prompt
from utils.vrchat_world import format_vrchat_world_text, search_vrchat_worlds
from guards.spam_guard import SpamGuard
from guards.mod_actions import ModActions


logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))
URL_RE = re.compile(r"https?://[^\s)>\"]+")

import random

_settings = get_settings()


def get_user_display_name(
    user_id: int, user_name: str, nicknames: dict[int, str]
) -> tuple[str, bool]:
    """
    ユーザーの表示名を取得（あだながあれば時々使う）

    Returns:
        (display_name, use_nickname) タプル
        - display_name: 使用する表示名
        - use_nickname: あだなを使用したかどうか
    """
    if user_id in nicknames:
        # 30% の確率であだなを使用
        if random.random() < 0.3:
            return nicknames[user_id], True
    return user_name, False


class MessageLogger(BaseCog):
    """
    メッセージログ＆会話処理

    機能:
    - 通常メッセージへのリアクション（キーワード検索）
    - メンション / リプライへの AI 応答（名前呼び対応）
    - `kenny-chat` のクロスサーバー中継
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        # kenny-chat: user_id -> last_post_ts
        self._kenny_chat_last_post: dict[int, float] = {}
        # kenny-chat: origin_msg_id -> [(channel_id, mirrored_msg_id), ...]
        self._kenny_chat_mirrors: dict[int, list[tuple[int, int]]] = {}
        # kenny-chat: mirrored_msg_id -> origin_msg_id
        self._kenny_chat_reverse: dict[int, int] = {}
        # AI応答のチャンネル単位クールダウン
        self._ai_channel_last: dict[int, float] = {}
        self._local_rag = LocalRAG(Path(__file__).resolve().parent.parent)
        self._live_info = LiveInfoService()
        self._model_ready_notifiers: set[tuple[int, int, str]] = set()
        self._vector_store = MessageVectorStore(MESSAGE_VECTOR_DB_PATH)
        self._ai_retry_countdowns = ChannelCountdown()
        self._ai_progress_countdowns = ChannelCountdown()

    def _extract_tool_calls(self, response: object) -> list[object]:
        if response is None:
            return []
        message = None
        if isinstance(response, dict):
            message = response.get("message", {})
        else:
            message = getattr(response, "message", None)
        if message is None:
            return []
        if isinstance(message, dict):
            return list(message.get("tool_calls") or [])
        return list(getattr(message, "tool_calls", None) or [])

    def _extract_message_content(self, response: object) -> str:
        if response is None:
            return ""
        message = None
        if isinstance(response, dict):
            message = response.get("message", {})
        else:
            message = getattr(response, "message", None)
        if message is None:
            return ""
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(getattr(message, "content", "") or "")

    def _response_message_payload(self, response: object) -> dict:
        if response is None:
            return {}
        message = None
        if isinstance(response, dict):
            message = response.get("message", {})
        else:
            message = getattr(response, "message", None)
        if isinstance(message, dict):
            return dict(message)
        if message is None:
            return {}

        payload: dict[str, object] = {"role": getattr(message, "role", "assistant")}
        content = getattr(message, "content", None)
        if content is not None:
            payload["content"] = content
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = tool_calls
        thinking = getattr(message, "thinking", None)
        if thinking:
            payload["thinking"] = thinking
        return payload

    def _normalize_tool_call(self, call: object) -> tuple[str, dict]:
        if isinstance(call, dict):
            fn = call.get("function") or {}
            name = str(fn.get("name") or "")
            args = fn.get("arguments") or {}
            return name, args if isinstance(args, dict) else {}
        fn = getattr(call, "function", None)
        if fn is None:
            return "", {}
        name = str(getattr(fn, "name", "") or "")
        args = getattr(fn, "arguments", None) or {}
        return name, args if isinstance(args, dict) else {}

    def _build_history_context(self, blocks: list[tuple[str, str]]) -> str:
        parts: list[str] = []
        for title, body in blocks:
            body = (body or "").strip()
            if not body:
                continue
            parts.append(f"[{title}]\n{body}")
        if not parts:
            return ""
        return "\n\n".join(parts) + "\n\n"

    def _extract_urls(self, text: str) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        for match in URL_RE.findall(text or ""):
            url = match.rstrip(".,]")
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    async def _embed_text(self, text: str) -> list[float] | None:
        embed_client = getattr(self.bot, "ollama_embed_client", self.bot.ollama_client)
        if not text or not embed_client.has_embed():
            return None
        try:
            model_name = self._cfg_str("ollama.model_embedding", "embeddinggemma")
            vectors = await asyncio.to_thread(embed_client.embed, model_name, text)
            return vectors[0] if vectors else None
        except Exception:
            logger.exception("Failed to embed text")
            return None

    async def _index_message_embedding(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        author_id: int,
        author: str,
        content: str,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        embedding = await self._embed_text(content)
        if not embedding:
            return
        timestamp = datetime.now(JST).isoformat()
        try:
            await asyncio.to_thread(
                self._vector_store.upsert_message,
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
                author_id=author_id,
                author=author,
                content=content,
                timestamp=timestamp,
                embedding=embedding,
            )
        except Exception:
            logger.exception("Failed to index message embedding")

    def _schedule_message_index(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        author_id: int,
        author: str,
        content: str,
    ) -> None:
        asyncio.create_task(
            self._index_message_embedding(
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
                author_id=author_id,
                author=author,
                content=content,
            )
        )

    def _context_target_candidates(
        self, msg: discord.Message
    ) -> dict[str, tuple[int, str]]:
        targets: dict[str, tuple[int, str]] = {
            "author": (
                msg.author.id,
                getattr(msg.author, "display_name", None)
                or msg.author.name
                or str(msg.author.id),
            )
        }
        if (
            msg.reference
            and msg.reference.resolved
            and isinstance(msg.reference.resolved, discord.Message)
        ):
            reply_author = msg.reference.resolved.author
            if not reply_author.bot and reply_author.id != msg.author.id:
                targets["replied_user"] = (
                    reply_author.id,
                    getattr(reply_author, "display_name", None)
                    or reply_author.name
                    or str(reply_author.id),
                )
        mention_index = 1
        for member in msg.mentions:
            if member.bot or member.id == msg.author.id:
                continue
            if any(existing_id == member.id for existing_id, _ in targets.values()):
                continue
            targets[f"mentioned_{mention_index}"] = (
                member.id,
                getattr(member, "display_name", None) or member.name or str(member.id),
            )
            mention_index += 1
        return targets

    async def _resolve_chat_context(
        self,
        *,
        msg: discord.Message,
        user_display: str,
        text: str,
    ) -> str:
        guild_id = msg.guild.id if msg.guild else 0
        channel_id = msg.channel.id
        user_id = msg.author.id
        guild_name = msg.guild.name if msg.guild else "DM"
        channel_name = (
            msg.channel.name if hasattr(msg.channel, "name") else str(msg.channel.id)
        )
        store = MessageStore(
            guild_id, channel_id, guild_name=guild_name, channel_name=channel_name
        )
        user_lines = self._cfg_int("chat.user_history_lines", 24)
        channel_lines = self._cfg_int("chat.channel_history_lines", 16)
        target_candidates = self._context_target_candidates(msg)

        def get_user_history(lines: int = user_lines) -> str:
            lines = max(1, min(int(lines or user_lines), max(1, user_lines)))
            return store.get_recent_context_for_user(user_id, lines=lines)

        def get_member_history(target: str = "author", lines: int = user_lines) -> str:
            """Get recent messages for a specific candidate user in this conversation."""
            target_key = (target or "author").strip().lower()
            target_info = (
                target_candidates.get(target_key) or target_candidates["author"]
            )
            lines = max(1, min(int(lines or user_lines), max(1, user_lines)))
            return store.get_recent_context_for_user(target_info[0], lines=lines)

        def get_channel_history(lines: int = channel_lines) -> str:
            lines = max(1, min(int(lines or channel_lines), max(1, channel_lines)))
            return store.get_recent_context(lines=lines)

        def get_recent_turns(lines: int = 6) -> str:
            """Get the most recent turns in this channel regardless of speaker."""
            lines = max(1, min(int(lines or 6), 12))
            return store.format_messages(store.get_recent_messages(lines=lines))

        def get_reply_chain(lines: int = 4) -> str:
            """Get a short reply chain centered on the referenced message and latest turns."""
            lines = max(1, min(int(lines or 4), 8))
            messages = store.get_recent_messages(lines=max(lines * 2, 6))
            if not messages:
                return ""

            if (
                msg.reference
                and msg.reference.resolved
                and isinstance(msg.reference.resolved, discord.Message)
            ):
                reference_id = msg.reference.resolved.id
                chain: list[dict] = []
                for item in messages:
                    if int(item.get("id", 0) or 0) == int(reference_id):
                        chain.append(item)
                chain.extend(messages[-lines:])
                deduped: list[dict] = []
                seen_ids: set[int] = set()
                for item in chain:
                    item_id = int(item.get("id", 0) or 0)
                    if item_id and item_id in seen_ids:
                        continue
                    if item_id:
                        seen_ids.add(item_id)
                    deduped.append(item)
                return store.format_messages(deduped[-lines:])
            return store.format_messages(messages[-lines:])

        def get_semantic_history(
            scope: str = "channel", k: int = 6, target: str = "author"
        ) -> str:
            """Search semantically related messages from the channel or a specific user."""
            return f"scope={scope}, k={k}, target={target}"

        def get_local_knowledge(
            query: str = "", limit: int = 4, capability_only: bool = False
        ) -> str:
            """Get relevant bot-local and channel-specific documentation from RAG files."""
            lookup = (query or text or "").strip()
            if not lookup:
                lookup = text
            return self._get_local_knowledge(
                lookup,
                limit=limit,
                capability_only=capability_only,
                max_chars=2200,
                channel_id=channel_id,
            )

        channel_profile_block = self._build_channel_profile_block(
            channel_id=channel_id,
            limit=4,
            max_chars=1800,
        )

        planner_messages = [
            {
                "role": "system",
                "content": (
                    "You are a context planner for a Discord bot.\n"
                    "Decide what context should be gathered before answering the user.\n"
                    "Available tools let you fetch recent message history, semantic history, and bot-local documentation.\n"
                    "Use get_reply_chain first for direct replies, short acknowledgements, clarification answers, or context that depends on the immediately previous turns.\n"
                    "Use get_recent_turns for short conversational context before considering semantic history.\n"
                    "Use get_member_history when the user asks about themselves, a replied user, or a mentioned user.\n"
                    "Use get_channel_history for shared discussion or recent events in the channel.\n"
                    "Use get_semantic_history only when topical similarity matters more than strict recency.\n"
                    "Avoid get_semantic_history for very short replies such as numbers, yes/no, which one, this/that, or direct answers to the bot's previous message.\n"
                    "Use get_local_knowledge when the user asks about bot functions, commands, setup, README contents, RAG behavior, or project-specific facts.\n"
                    "Use get_local_knowledge when the user asks about channel rules, channel FAQ, channel procedures, or any channel-specific knowledge that has been added to RAG.\n"
                    "Use _get_bot_game_catalog for questions about available games or game-related utility commands.\n"
                    "Use _get_bot_command_catalog for questions asking what commands or features the bot has.\n"
                    "Use _get_runtime_model_info when the user asks which model is currently configured or being used.\n"
                    "Use _search_vrchat_world when the user wants VRChat world search results.\n"
                    "When a channel profile is available, treat it as the authoritative description of that channel.\n"
                    "Do not let older generic replies override the channel profile.\n"
                    "You may call multiple tools if needed. If the message is self-contained, call no tools."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"user_id={user_id}\n"
                    f"user_display={user_display}\n"
                    f"channel_id={channel_id}\n"
                    f"user_history_limit={user_lines}\n"
                    f"channel_history_limit={channel_lines}\n"
                    f"available_targets={json.dumps({k: {'user_id': v[0], 'display': v[1]} for k, v in target_candidates.items()}, ensure_ascii=False)}\n"
                    f"message={text}"
                ),
            },
        ]
        if channel_profile_block:
            planner_messages[0]["content"] = (
                f"{channel_profile_block}\n\n" + str(planner_messages[0]["content"])
            )

        blocks: list[tuple[str, str]] = []
        try:
            response = await asyncio.to_thread(
                self.bot.ollama_client.chat,
                model=self._cfg_str("ollama.model_default", "gpt-oss:120b"),
                messages=planner_messages,
                stream=False,
                tools=[
                    get_recent_turns,
                    get_reply_chain,
                    get_user_history,
                    get_member_history,
                    get_channel_history,
                    get_semantic_history,
                    get_local_knowledge,
                    self._get_bot_game_catalog,
                    self._get_bot_command_catalog,
                    self._get_runtime_model_info,
                    self._search_vrchat_world,
                ],
            )
            tool_calls = self._extract_tool_calls(response)
            if not tool_calls:
                return ""

            for call in tool_calls:
                name, args = self._normalize_tool_call(call)
                requested_lines = args.get("lines")
                if name == "get_user_history":
                    body = get_user_history(
                        requested_lines
                        if isinstance(requested_lines, int)
                        else user_lines
                    )
                    if body:
                        blocks.append(
                            (f"このユーザーの最近の発言 {user_lines} 件以内", body)
                        )
                elif name == "get_recent_turns":
                    body = get_recent_turns(
                        requested_lines if isinstance(requested_lines, int) else 6
                    )
                    if body:
                        blocks.append(("このチャンネルの直近会話", body))
                elif name == "get_reply_chain":
                    body = get_reply_chain(
                        requested_lines if isinstance(requested_lines, int) else 4
                    )
                    if body:
                        blocks.append(("直前の会話チェーン", body))
                elif name == "get_member_history":
                    target = str(args.get("target") or "author")
                    target_key = target.strip().lower()
                    body = get_member_history(
                        target=target_key,
                        lines=requested_lines
                        if isinstance(requested_lines, int)
                        else user_lines,
                    )
                    target_info = (
                        target_candidates.get(target_key) or target_candidates["author"]
                    )
                    if body:
                        blocks.append(
                            (f"{target_info[1]} の最近の発言 {user_lines} 件以内", body)
                        )
                elif name == "get_channel_history":
                    body = get_channel_history(
                        requested_lines
                        if isinstance(requested_lines, int)
                        else channel_lines
                    )
                    if body:
                        blocks.append(
                            (
                                f"このチャンネル全体の最近の発言 {channel_lines} 件以内",
                                body,
                            )
                        )
                elif name == "get_semantic_history":
                    scope = str(args.get("scope") or "channel")
                    target = str(args.get("target") or "author")
                    k = args.get("k")
                    query_embedding = await self._embed_text(text)
                    if not query_embedding:
                        continue
                    scope_value = scope.strip().lower()
                    limit = max(
                        1,
                        min(
                            int(
                                k
                                if isinstance(k, int)
                                else self._cfg_int("chat.semantic_history_k", 6)
                            ),
                            12,
                        ),
                    )
                    target_key = target.strip().lower()
                    target_info = (
                        target_candidates.get(target_key) or target_candidates["author"]
                    )
                    rows = await asyncio.to_thread(
                        self._vector_store.semantic_search,
                        guild_id=guild_id,
                        channel_id=channel_id,
                        query_embedding=query_embedding,
                        author_id=target_info[0] if scope_value == "user" else None,
                        limit=limit,
                    )
                    body = self._vector_store.format_results(rows)
                    if body:
                        title = (
                            f"{target_info[1]} の意味的に近い過去発言"
                            if scope_value == "user"
                            else "このチャンネルの意味的に近い過去発言"
                        )
                        blocks.append((title, body))
                elif name == "get_local_knowledge":
                    query = str(args.get("query") or text)
                    limit = args.get("limit")
                    capability_only = bool(args.get("capability_only", False))
                    body = get_local_knowledge(
                        query=query,
                        limit=int(limit) if isinstance(limit, int) else 4,
                        capability_only=capability_only,
                    )
                    if body:
                        blocks.append(("Bot ローカル資料", body))
                elif name == "_get_runtime_model_info":
                    body = self._get_runtime_model_info()
                    if body:
                        blocks.append(("現在のモデル設定", body))
                elif name == "_get_bot_game_catalog":
                    body = self._get_bot_game_catalog()
                    if body:
                        blocks.append(("Bot ゲーム一覧", body))
                elif name == "_get_bot_command_catalog":
                    body = self._get_bot_command_catalog(
                        str(args.get("category") or "")
                    )
                    if body:
                        blocks.append(("Bot コマンド一覧", body))
                elif name == "_search_vrchat_world":
                    body = self._search_vrchat_world(
                        keyword=str(args.get("keyword") or text or ""),
                        count=int(args.get("count") or 5),
                        author=str(args.get("author") or ""),
                        tag=str(args.get("tag") or ""),
                    )
                    if body:
                        blocks.append(("VRChat ワールド検索結果", body))
        except Exception:
            logger.exception("Failed to resolve chat context via tool calling")

        if not blocks:
            query_embedding = await self._embed_text(text)
            if query_embedding:
                rows = await asyncio.to_thread(
                    self._vector_store.semantic_search,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    query_embedding=query_embedding,
                    author_id=None,
                    limit=max(1, min(self._cfg_int("chat.semantic_history_k", 6), 12)),
                )
                body = self._vector_store.format_results(rows)
                if body:
                    blocks.append(("このチャンネルの意味的に近い過去発言", body))

        if channel_profile_block:
            blocks.insert(0, ("このチャンネルの正式プロフィール", channel_profile_block))

        return self._build_history_context(blocks)

    async def _run_ollama_chat_with_tools(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[object],
        max_rounds: int = 4,
        guild: discord.Guild | None = None,
        channel_id: int | None = None,
        user_id: int | None = None,
    ) -> str | None:
        if not tools:
            response = await asyncio.to_thread(
                self.bot.ollama_client.chat,
                model=model,
                messages=messages,
                stream=False,
            )
            return self._extract_message_content(response)

        working_messages = [dict(item) for item in messages]
        source_urls: list[str] = []
        last_tool_outputs: list[tuple[str, str]] = []
        for _ in range(max_rounds):
            response = await asyncio.to_thread(
                self.bot.ollama_client.chat,
                model=model,
                messages=working_messages,
                stream=False,
                tools=tools,
            )
            assistant_message = self._response_message_payload(response)
            if assistant_message:
                working_messages.append(assistant_message)

            tool_calls = self._extract_tool_calls(response)
            if not tool_calls:
                answer = self._extract_message_content(response)
                if answer and source_urls:
                    logger.info(
                        "Appending source URLs to response: %s", source_urls[:8]
                    )
                    refs = "\n".join(f"- {url}" for url in source_urls[:8])
                    answer = f"{answer.rstrip()}\n\n参考元:\n{refs}"
                return answer

            async def execute_tool_call(call: object) -> tuple[dict, list[str]]:
                name, args = self._normalize_tool_call(call)
                tool_fn = next(
                    (tool for tool in tools if getattr(tool, "__name__", "") == name),
                    None,
                )
                if tool_fn is None:
                    return (
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": f"Tool {name} not found",
                        },
                        [],
                    )
                try:
                    result = await asyncio.to_thread(tool_fn, **args)
                    result_text = str(result)
                    found_urls: list[str] = []
                    if name in {"web_search", "web_fetch"}:
                        logger.info("Web tool used: %s args=%s", name, args)
                        await send_event_log(
                            self.bot,
                            guild=guild,
                            title="Web Tool 利用",
                            description=f"`{name}` が実行されました。",
                            fields=[
                                ("ユーザーID", str(user_id or 0), True),
                                ("チャンネルID", str(channel_id or 0), True),
                                ("引数", str(args)[:1000], False),
                            ],
                        )
                        found_urls = self._extract_urls(result_text)
                except Exception as e:
                    logger.exception("Tool call failed: %s", name)
                    result_text = f"Tool {name} failed: {e}"
                    found_urls = []
                if len(result_text) > 8000:
                    result_text = result_text[:8000] + "\n...(省略)..."
                return (
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": result_text,
                    },
                    found_urls,
                )

            results = await asyncio.gather(
                *(execute_tool_call(call) for call in tool_calls)
            )
            for tool_message, found_urls in results:
                working_messages.append(tool_message)
                last_tool_outputs.append(
                    (
                        str(tool_message.get("tool_name") or ""),
                        str(tool_message.get("content") or ""),
                    )
                )
                for url in found_urls:
                    if url not in source_urls:
                        source_urls.append(url)

        answer = self._extract_message_content(response)
        if not answer and last_tool_outputs:
            successful_searches: list[str] = []
            tool_summaries: list[str] = []
            user_request = ""
            for item in reversed(messages):
                if str(item.get("role") or "") == "user":
                    user_request = str(item.get("content") or "").strip()
                    break
            for tool_name, content in last_tool_outputs[-6:]:
                text = strip_ansi_and_ctrl(content or "").strip()
                if not text:
                    continue
                if tool_name == "web_search" and "failed:" not in text.lower():
                    successful_searches.append(text[:1500])
                    continue
                if tool_name == "web_fetch" and "failed:" in text.lower():
                    continue
                if len(text) > 300:
                    text = text[:300] + "..."
                tool_summaries.append(f"- {tool_name}: {text}")
            if successful_searches:
                retry_prompt = get_prompt("chat", "tool_retry_prompt").format(
                    user_request=user_request or "指定なし",
                    search_results=chr(10).join(successful_searches[:2]),
                )
                try:
                    answer = strip_ansi_and_ctrl(
                        (
                            await asyncio.to_thread(
                                self.bot.ollama_client.chat_simple,
                                model=model,
                                prompt=retry_prompt,
                                stream=False,
                            )
                            or ""
                        ).strip()
                    )
                except Exception:
                    logger.exception(
                        "Failed to synthesize answer from web_search fallback"
                    )
                    answer = ""
                if not answer:
                    answer = "外部検索結果をもとに回答します。\n\n" + "\n\n".join(
                        successful_searches[:2]
                    )
            elif tool_summaries:
                answer = (
                    "外部情報の取得は試しましたが、回答文をうまく生成できませんでした。\n"
                    "取得結果:\n" + "\n".join(tool_summaries)
                )
        if answer and source_urls:
            logger.info("Appending source URLs to response: %s", source_urls[:8])
            refs = "\n".join(f"- {url}" for url in source_urls[:8])
            answer = f"{answer.rstrip()}\n\n参考元:\n{refs}"
        return answer

    async def _run_ollama_text(
        self, model: str, prompt: str, *, timeout_sec: int | None = None
    ) -> str | None:
        effective_timeout = timeout_sec
        if effective_timeout is None or effective_timeout <= 0:
            effective_timeout = self._cfg_int("ollama.timeout_sec", 180)
        return await asyncio.wait_for(
            asyncio.to_thread(
                self.bot.ollama_client.chat_simple,
                model=model,
                prompt=prompt,
                stream=False,
            ),
            timeout=effective_timeout,
        )

    def _is_model_available(self, model: str) -> bool:
        try:
            listing = self.bot.ollama_client.client.list()
            models = listing.get("models", []) if isinstance(listing, dict) else []
            wanted = model.strip()
            for item in models:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("model") or item.get("name") or "").strip()
                if name == wanted:
                    return True
            return False
        except Exception:
            return False

    async def _notify_when_model_ready(
        self,
        channel: discord.abc.Messageable,
        *,
        channel_id: int,
        user_id: int,
        mention: str,
        model: str,
    ) -> None:
        key = (channel_id, user_id, model)
        if key in self._model_ready_notifiers:
            return
        self._model_ready_notifiers.add(key)
        try:
            for _ in range(240):
                ready = await asyncio.to_thread(self._is_model_available, model)
                if ready:
                    await channel.send(
                        f"{mention}\nモデル `{model}` の準備が完了しました。もう一度話しかけてください。"
                    )
                    return
                await asyncio.sleep(15)
        finally:
            self._model_ready_notifiers.discard(key)

    def _cfg_int(self, path: str, default: int) -> int:
        try:
            return int(_settings.get(path, default))
        except Exception:
            return default

    def _cfg_str(self, path: str, default: str) -> str:
        try:
            return str(_settings.get(path, default))
        except Exception:
            return default

    def _cfg_map(self, path: str) -> dict:
        v = _settings.get(path, {})
        return v if isinstance(v, dict) else {}

    def _cfg_nicknames(self) -> dict[int, str]:
        raw = self._cfg_map("user_nicknames")
        out: dict[int, str] = {}
        for k, v in raw.items():
            try:
                out[int(k)] = str(v)
            except Exception:
                continue
        return out

    def _is_kenny_chat(self, msg: discord.Message) -> bool:
        return (
            isinstance(msg.channel, discord.TextChannel)
            and msg.channel.name == "kenny-chat"
        )

    def _initial_of(self, member: discord.abc.User) -> str:
        name = ""
        if isinstance(member, discord.Member):
            name = member.display_name or member.name or ""
        else:
            name = (
                member.display_name if hasattr(member, "display_name") else member.name
            )
        name = (name or "").strip()
        return name[0].upper() if name else "?"

    def _collect_bridge_text(self, msg: discord.Message) -> str:
        parts: list[str] = []
        content = (msg.content or "").strip()
        if content:
            parts.append(content)
        for a in msg.attachments:
            parts.append(a.url)
        out = "\n".join(parts).strip()
        if len(out) > 1700:
            out = out[:1700] + "\n...(省略)..."
        return out

    def _is_capability_query(self, text: str) -> bool:
        t = (text or "").lower()
        keys = (
            "どういう機能",
            "何ができる",
            "できること",
            "使い方",
            "機能を教えて",
            "きのうを教えて",
            "君の機能",
            "君のきのう",
            "あなたの機能",
            "あなたのきのう",
            "お前の機能",
            "このbotの機能",
            "kennybotの機能",
            "kenny botの機能",
            "最新更新",
            "更新内容",
            "アップデート",
            "変更点",
            "changelog",
            "help",
        )
        return any(k in t for k in keys)

    def _is_channel_profile_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        capability_terms = (
            "機能",
            "コマンド",
            "できること",
            "使い方",
            "help",
            "ゲーム",
            "更新",
            "変更点",
            "アップデート",
        )
        if any(term in normalized for term in capability_terms):
            return False
        profile_terms = (
            "サーバー",
            "チャンネル",
            "ワールド",
            "このサーバー",
            "このチャンネル",
            "このワールド",
            "ここ",
            "この場所",
            "何のやつ",
            "なんのやつ",
            "何する",
            "何をする",
            "どんな場所",
            "用途",
            "目的",
            "概要",
            "説明",
            "何の場",
        )
        return any(term in normalized for term in profile_terms)

    def _is_runtime_model_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        model_keys = tuple(
            normalize_keyword_match_text(key)
            for key in ("model", "モデル", "aiモデル", "使用モデル", "利用モデル")
        )
        current_keys = tuple(
            normalize_keyword_match_text(key)
            for key in (
                "今",
                "いま",
                "現在",
                "使用",
                "利用",
                "使用中",
                "利用中",
                "使ってる",
                "使っている",
                "つかっている",
                "使って",
                "使う",
                "使われている",
                "チャットで",
                "会話で",
                "通常会話",
                "デフォルト",
                "既定",
                "何",
                "どれ",
                "教えて",
            )
        )
        return any(key in normalized for key in model_keys) and any(
            key in normalized for key in current_keys
        )

    def _is_bot_capability_or_game_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        capability_keys = (
            "何ができる",
            "できること",
            "機能",
            "使い方",
            "コマンド",
            "help",
        )
        game_keys = (
            "ゲーム",
            "遊べる",
            "人狼",
            "わーどうるふ",
            "ワードウルフ",
            "あいうえお",
            "timer",
            "vc_control",
            "group_match",
        )
        bot_keys = ("kennybot", "kenny bot", "このbot", "あなた", "君", "お前")
        return any(key in normalized for key in capability_keys) or (
            any(key in normalized for key in game_keys)
            and any(key in normalized for key in bot_keys)
        )

    def _sanitize_for_prompt(self, text: str, max_len: int) -> str:
        v = strip_ansi_and_ctrl(text or "")
        v = v.replace("@everyone", "＠everyone").replace("@here", "＠here")
        if max_len > 0 and len(v) > max_len:
            return v[:max_len]
        return v

    def _build_external_context_text(self, contexts: list[ExternalContext]) -> str:
        if not contexts:
            return ""
        blocks = [f"[{item.label}]\n{item.body}" for item in contexts]
        return "\n\n".join(blocks)

    def _get_channel_knowledge(
        self,
        *,
        channel_id: int | None,
        limit: int = 4,
        max_chars: int = 1200,
    ) -> str:
        if not channel_id:
            return ""
        chunks = self._local_rag.retrieve(
            "",
            limit=max(1, min(int(limit or 4), 6)),
            channel_id=channel_id,
            channel_only=True,
        )
        blocks: list[str] = []
        for chunk in chunks:
            body = chunk.body.strip()
            if max_chars > 0 and len(body) > max_chars:
                body = body[:max_chars] + "\n...(省略)..."
            blocks.append(f"[{chunk.source} / {chunk.title}]\n{body}")
        return "\n\n".join(blocks)

    def _build_channel_profile_block(
        self,
        *,
        channel_id: int | None,
        limit: int = 4,
        max_chars: int = 1800,
    ) -> str:
        knowledge = self._get_channel_knowledge(
            channel_id=channel_id,
            limit=limit,
            max_chars=max_chars,
        )
        if not knowledge:
            return ""
        return (
            "[このチャンネルの正式プロフィール]\n"
            "以下はこのチャンネルの前提です。一般テンプレート、古い assistant 発言、"
            "推測よりも優先して扱ってください。\n"
            "この内容と矛盾する場合は、こちらを正としてください。\n\n"
            f"{knowledge}"
        )

    async def _answer_channel_profile_query(
        self,
        channel: discord.abc.Messageable,
        query: str,
        mention: str | None = None,
        source_msg: discord.Message | None = None,
        *,
        channel_id: int | None = None,
    ) -> None:
        channel_id = int(channel_id or getattr(channel, "id", 0))
        if self._is_ai_channel_rate_limited(channel_id):
            prefix = f"{mention}\n" if mention else ""
            await channel.send(
                f"{prefix}このチャンネルではAI応答の間隔制限中です。数秒待ってから再実行してください。"
            )
            return

        channel_profile_block = self._build_channel_profile_block(
            channel_id=channel_id,
            limit=6,
            max_chars=2600,
        )
        if not channel_profile_block:
            prefix = f"{mention}\n" if mention else ""
            await channel.send(
                f"{prefix}この場所の説明はまだ登録されていません。"
            )
            return

        progress_key = f"ai-progress:{channel_id}:profile:{mention or 'anon'}"
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        prompt = get_prompt("chat", "channel_profile_prompt").format(
            query=query,
            channel_profile_block=channel_profile_block,
        )
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")

        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=channel,
                mention_user_id=0,
                text_factory=lambda elapsed: self.bot.ai_progress_tracker.render(
                    ticket, elapsed
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            try:
                async with channel.typing():
                    answer = await self._run_ollama_text(
                        model=model_name,
                        prompt=prompt,
                    )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = (
                strip_ansi_and_ctrl((answer or "").strip())
                or "この場所の説明を作れませんでした。"
            )
            prefix = f"{mention}\n" if mention else ""
            await self._send_chunked_text(channel, answer, prefix=prefix)
            if source_msg is not None:
                await self._log_ai_output_event(
                    source_msg,
                    output_text=answer,
                    input_text=query,
                    title="AI サーバー説明応答",
                    description="サーバー・チャンネル・ワールドの説明に応答しました。",
                )
        except Exception as e:
            prefix = f"{mention}\n" if mention else ""
            await send_event_log(
                self.bot,
                level="error",
                title="サーバー説明生成失敗",
                description="サーバー・チャンネル・ワールドの説明生成に失敗しました。",
                fields=[
                    ("チャンネル", str(channel_id), True),
                    ("クエリ", query[:1000], False),
                    ("エラー", str(e)[:1000], False),
                ],
            )
            await channel.send(
                f"{prefix}サーバー説明の生成に失敗しました。\n```{str(e)[:180]}```"
            )
        finally:
            await self._ai_progress_countdowns.stop(progress_key, delete_message=True)

    def _get_local_knowledge(
        self,
        query: str,
        limit: int = 4,
        *,
        capability_only: bool = False,
        max_chars: int = 1200,
        channel_id: int | None = None,
    ) -> str:
        query = (query or "").strip()
        if not query:
            return ""
        limit = max(1, min(int(limit or 4), 6))
        chunks = self._local_rag.retrieve(
            query,
            limit=limit,
            capability_only=capability_only,
            channel_id=channel_id,
        )
        blocks: list[str] = []
        for chunk in chunks:
            body = chunk.body.strip()
            if max_chars > 0 and len(body) > max_chars:
                body = body[:max_chars] + "\n...(省略)..."
            blocks.append(f"[{chunk.source} / {chunk.title}]\n{body}")
        return "\n\n".join(blocks)

    def _should_send_letter_file(self, text: str) -> bool:
        return "ぽっぷこーんきめら" in normalize_keyword_match_text(text or "")

    async def _send_letter_file(self, msg: discord.Message, answer: str) -> None:
        display_name = (
            getattr(msg.author, "display_name", None) or msg.author.name or "user"
        ).strip() or "user"
        filename = f"{display_name}への手紙.txt"
        payload = io.BytesIO(answer.encode("utf-8"))
        discord_file = discord.File(payload, filename=filename)
        await msg.channel.send(
            content=f"{msg.author.mention}\n{answer}",
            file=discord_file,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _is_ai_channel_rate_limited(self, channel_id: int) -> bool:
        now = time.time()
        cooldown = float(self._cfg_int("security.ai_channel_cooldown_seconds", 4))
        last = self._ai_channel_last.get(channel_id, 0.0)
        if now - last < cooldown:
            return True
        self._ai_channel_last[channel_id] = now
        return False

    async def _handle_dm_message(self, msg: discord.Message) -> None:
        # DMアクティビティをイベントログに記録
        author_name = (
            msg.author.display_name
            if hasattr(msg.author, "display_name")
            else msg.author.name
        )
        user_content = (msg.content or "").strip()
        await send_event_log(
            self.bot,
            guild=None,
            level="info",
            title="DM 受信",
            description=f"{msg.author.mention} からDMを受信しました",
            fields=[
                ("ユーザー", f"{author_name} ({msg.author.id})", False),
                ("内容", user_content[:500] if user_content else "(empty)", False),
            ],
        )

        # 総合ログに記録
        log_user_message(msg)

        text = normalize_user_text(msg.content or "")
        if not text:
            return
        text = self._sanitize_for_prompt(
            text,
            self._cfg_int("security.max_user_message_chars", 1200),
        )

        await self._log_ai_input_event(msg, text=text, title="DM AI 入力")

        if self._is_runtime_model_query(text):
            await self._send_runtime_model_reply(
                msg.channel,
                source_msg=msg,
                input_text=text,
            )
            return

        if self._is_capability_query(text):
            await self._answer_capability_query(
                msg.channel,
                text,
                source_msg=msg,
                channel_id=msg.channel.id,
            )
            return

        if self._is_ai_channel_rate_limited(msg.channel.id):
            await msg.channel.send("少し待ってから送ってください。")
            return

        store = MessageStore(
            0,
            msg.channel.id,
            guild_name="DM",
            channel_name=f"DM:{msg.author.name}",
        )
        user_name = (
            msg.author.display_name
            if hasattr(msg.author, "display_name")
            else msg.author.name
        )
        store.add_message(
            user_name or str(msg.author.id), text, msg.id, author_id=msg.author.id
        )
        self._schedule_message_index(
            guild_id=0,
            channel_id=msg.channel.id,
            message_id=msg.id,
            author_id=msg.author.id,
            author=user_name or str(msg.author.id),
            content=text,
        )

        history_context = await self._resolve_chat_context(
            msg=msg,
            user_display=user_name or str(msg.author.id),
            text=text,
        )
        if not history_context:
            history_lines = self._cfg_int("chat.history_lines", 100)
            history_text = store.get_recent_context(lines=history_lines)
            history_context = (
                HISTORY_CONTEXT_TEMPLATE.format(history=history_text)
                if history_text
                else ""
            )
        external_context = ""
        if self._live_info.needs_external_context(text):
            external_context = self._build_external_context_text(
                await asyncio.to_thread(self._live_info.build_context, text)
            )
        prompt = PROMPT_TEMPLATE.format(
            user_display=user_name or str(msg.author.id),
            history_context=history_context
            + (f"[外部参照情報]\n{external_context}\n\n" if external_context else ""),
            user_message=text,
            max_response_length_prompt=self._cfg_int(
                "chat.max_response_length_prompt", 500
            ),
        )
        progress_key = f"ai-progress:{msg.channel.id}:{msg.author.id}"
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        ticket = await self.bot.ai_progress_tracker.create_ticket()

        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=msg.channel,
                mention_user_id=msg.author.id,
                text_factory=lambda elapsed: self.bot.ai_progress_tracker.render(
                    ticket, elapsed
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            try:
                async with msg.channel.typing():
                    answer = await self._run_ollama_text(
                        model=model_name,
                        prompt=prompt,
                    )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = strip_ansi_and_ctrl((answer or "").strip()) or "(応答が空でした)"
            max_len = self._cfg_int("chat.max_response_length", 1800)
            if len(answer) > max_len:
                answer = answer[:max_len] + "\n...(省略)..."

            bot_name = self.bot.user.name if self.bot.user else "Bot"
            bot_id = self.bot.user.id if self.bot.user else 0
            store.add_message(bot_name, answer, msg.id, author_id=bot_id)
            await msg.channel.send(answer)

            # 総合ログにAI応答を記録
            log_ai_output(
                msg.author,
                response=answer,
                model=model_name,
                msg=msg,
            )

            await self._log_ai_output_event(
                msg,
                output_text=answer,
                input_text=text,
                title="DM AI 応答成功",
                description="DM の AI 応答を送信しました。",
            )
        except Exception as e:
            logger.exception("DM AI response failed")

            # エラーも総合ログに記録
            log_ai_output(
                msg.author,
                response="",
                model=model_name,
                msg=msg,
                error=str(e)[:200],
            )

            await self._log_ai_output_event(
                msg,
                level="error",
                title="DM AI 応答失敗",
                description="DM の AI 応答処理中にエラーが発生しました。",
                input_text=text,
                error_text=str(e),
            )
            if isinstance(e, asyncio.TimeoutError):
                model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
                await msg.channel.send("モデル準備中です。完了したら通知します。")
                asyncio.create_task(
                    self._notify_when_model_ready(
                        msg.channel,
                        channel_id=msg.channel.id,
                        user_id=msg.author.id,
                        mention=msg.author.mention,
                        model=model_name,
                    )
                )
            else:
                await msg.channel.send(
                    f"内部エラーが発生しました。\n```\n{str(e)[:180]}\n```"
                )
        finally:
            await self._ai_progress_countdowns.stop(progress_key, delete_message=True)

    async def _handle_spam_violation(
        self, msg: discord.Message, content: str, level: str, violation_count: int
    ) -> None:
        await ModActions.delete_message(msg, f"スパム（レベル: {level}）")

        member = (
            msg.author
            if isinstance(msg.author, discord.Member)
            else await msg.guild.fetch_member(msg.author.id)
        )
        punishment_result = ""
        if member and level != "warning":
            action_result = await ModActions.execute_level(
                self.bot, msg.guild, member, level
            )
            if action_result.success:
                punishment_result = f"✅ 処罰実行: {action_result.action}"
                if action_result.detail:
                    punishment_result += f"\n{action_result.detail[:140]}"
            else:
                detail = (
                    action_result.detail
                    or "権限・ロール階層・対象状態を確認してください。"
                )
                punishment_result = f"❌ 処罰実行失敗: {level}\n理由: {detail[:140]}"

        spam_log_msg = await send_event_log(
            self.bot,
            guild=msg.guild,
            level="error",
            title="🚨 スパム検出",
            description=f"ユーザー {msg.author.mention} のスパムを検出しました。",
            fields=[
                (
                    "ユーザー情報",
                    f"名前: {msg.author.display_name or msg.author.name}\nID: {msg.author.id}",
                    False,
                ),
                (
                    "削除内容",
                    f"```{content[:200]}{'...' if len(content) > 200 else ''}```",
                    False,
                ),
                ("違反情報", f"レベル: **{level}**\n違反回数: {violation_count}", True),
                ("処罰", punishment_result if punishment_result else "警告のみ", True),
            ],
            footer=f"チャンネル: {msg.channel.name}",
        )
        if spam_log_msg is not None:
            await spam_log_msg.add_reaction("🔄")

        guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        if guard.should_warn(msg.author.id):
            warn_msg = (
                f"⚠️ {msg.author.mention}\n"
                f"スパムが検出されました。\n"
                f"現在のレベル: **{level}** (違反 {violation_count} 回)\n"
                "⚠️ 継続するとキックやバンの対象になります。"
            )
            await msg.channel.send(
                warn_msg,
                delete_after=15,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    def _read_readme_excerpt(self, max_chars: int = 6000) -> str:
        try:
            root = Path(__file__).resolve().parent.parent
            p = root / "README.md"
            txt = p.read_text(encoding="utf-8", errors="ignore")
            txt = txt.strip()
            if len(txt) > max_chars:
                txt = txt[:max_chars] + "\n...(省略)..."
            return txt
        except Exception as e:
            return f"README 取得失敗: {e}"

    def _read_git_updates(self, count: int = 8) -> str:
        try:
            root = Path(__file__).resolve().parent.parent
            cp = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "log",
                    f"-n{count}",
                    "--date=iso",
                    "--pretty=format:%h | %ad | %s",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            out = (cp.stdout or "").strip()
            if out:
                return out
            err = (cp.stderr or "").strip()
            return f"git log 取得失敗: {err or 'no output'}"
        except Exception as e:
            return f"git log 実行失敗: {e}"

    def _format_git_updates(self, count: int = 4) -> str:
        raw = self._read_git_updates(count=count)
        if raw.startswith("git log "):
            return raw
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return "\n".join(f"- {line}" for line in lines[:count])

    def _is_update_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        keys = (
            "最新更新",
            "更新内容",
            "アップデート",
            "変更点",
            "changelog",
            "更新履歴",
        )
        return any(key in normalized for key in keys)

    def _build_command_catalog_context(self) -> str:
        blocks: list[str] = []
        for section in HELP_SECTIONS:
            blocks.append(f"[HELP / {section.title}]\n" + "\n".join(section.lines))

        commands_by_category: dict[str, list[str]] = {
            category: [] for category in COMMAND_CATEGORY_ORDER
        }
        for meta in SLASH_COMMANDS.values():
            commands_by_category.setdefault(meta.category, []).append(
                f"/{meta.name}: {meta.description}"
            )
        for category in COMMAND_CATEGORY_ORDER:
            lines = commands_by_category.get(category, [])
            if lines:
                blocks.append(f"[HELP / コマンド {category}]\n" + "\n".join(lines))
        return "\n\n".join(blocks)

    def _get_bot_command_catalog(self, category: str = "") -> str:
        """Get the bot's confirmed help sections and slash commands."""
        wanted = (category or "").strip()
        blocks: list[str] = []
        for section in HELP_SECTIONS:
            if wanted and wanted not in section.title:
                continue
            blocks.append(f"[HELP / {section.title}]\n" + "\n".join(section.lines))

        commands_by_category: dict[str, list[str]] = {
            name: [] for name in COMMAND_CATEGORY_ORDER
        }
        for meta in SLASH_COMMANDS.values():
            commands_by_category.setdefault(meta.category, []).append(
                f"/{meta.name}: {meta.description}"
            )
        for name in COMMAND_CATEGORY_ORDER:
            if wanted and wanted not in name:
                continue
            lines = commands_by_category.get(name, [])
            if lines:
                blocks.append(f"[HELP / コマンド {name}]\n" + "\n".join(lines))
        return "\n\n".join(blocks)

    def _get_bot_game_catalog(self) -> str:
        """Get confirmed game and utility commands for this bot."""
        return (
            "[ゲーム]\n"
            "/game: ミニゲームを開始（リアクション参加）\n"
            "- mode=配布: 数字\n"
            "- mode=配布: 単語\n"
            "- mode=ワードウルフ\n"
            "- mode=人狼役職配布\n"
            "- mode=あいうえおバトル\n\n"
            "[ゲーム・ユーティリティ]\n"
            "/timer: タイマーを開始（時/分/秒指定）\n"
            "/vc_control: VCミュート操作パネルを作成\n"
            "/group_match: リアクション参加で2人組/3人組を自動作成\n"
        )

    def _get_runtime_model_info(self) -> str:
        """Get the user-facing current chat model without exposing internal settings."""
        default_model = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        return (
            f"今チャットで使っているモデルは `{default_model}` です。\n"
            "利用可能なモデルは `/model_list`、変更は `/model_change` で確認できます。"
        )

    def _truncate_event_text(self, value: str, limit: int = 1000) -> str:
        text = strip_ansi_and_ctrl((value or "").strip())
        if len(text) <= limit:
            return text
        return text[:limit] + "\n...(省略)..."

    async def _log_ai_input_event(
        self,
        msg: discord.Message,
        *,
        text: str,
        title: str = "AI 入力",
    ) -> None:
        await send_event_log(
            self.bot,
            guild=msg.guild,
            level="info",
            title=title,
            description="AI 応答対象のユーザー入力を受信しました。",
            fields=[
                ("ユーザー", f"{msg.author} ({msg.author.id})", False),
                (
                    "チャンネル",
                    f"{getattr(msg.channel, 'name', 'DM')} ({getattr(msg.channel, 'id', 0)})",
                    False,
                ),
                ("メッセージID", str(msg.id), True),
                ("内容", self._truncate_event_text(text), False),
            ],
        )

    async def _log_ai_output_event(
        self,
        msg: discord.Message,
        *,
        output_text: str = "",
        level: str = "success",
        title: str = "AI 応答",
        description: str = "AI 応答を送信しました。",
        input_text: str = "",
        error_text: str = "",
    ) -> None:
        fields: list[tuple[str, str, bool]] = [
            ("ユーザー", f"{msg.author} ({msg.author.id})", False),
            (
                "チャンネル",
                f"{getattr(msg.channel, 'name', 'DM')} ({getattr(msg.channel, 'id', 0)})",
                False,
            ),
            ("メッセージID", str(msg.id), True),
        ]
        if input_text:
            fields.append(("入力", self._truncate_event_text(input_text), False))
        if output_text:
            fields.append(("応答", self._truncate_event_text(output_text), False))
        if error_text:
            fields.append(("エラー", self._truncate_event_text(error_text), False))
        await send_event_log(
            self.bot,
            guild=msg.guild,
            level=level,
            title=title,
            description=description,
            fields=fields,
        )

    async def _send_runtime_model_reply(
        self,
        channel: discord.abc.Messageable,
        *,
        mention: str | None = None,
        source_msg: discord.Message | None = None,
        input_text: str = "",
    ) -> None:
        prefix = f"{mention}\n" if mention else ""
        answer = self._get_runtime_model_info()
        await self._send_chunked_text(
            channel,
            answer,
            prefix=prefix,
        )
        if source_msg is not None:
            await self._log_ai_output_event(
                source_msg,
                output_text=answer,
                input_text=input_text,
                title="AI 応答",
                description="モデル問い合わせへ応答しました。",
            )

    def _search_vrchat_world(
        self,
        keyword: str,
        count: int = 5,
        author: str = "",
        tag: str = "",
    ) -> str:
        """Search VRChat worlds using the existing api/vrchat implementation."""
        query = (keyword or "").strip()
        if not query:
            return "keyword is required"
        safe_count = max(1, min(int(count or 5), 10))
        formatter, worlds = search_vrchat_worlds(
            query,
            safe_count,
            (author or "").strip() or None,
            (tag or "").strip() or None,
        )
        if not worlds:
            return "該当するワールドが見つかりませんでした。"
        return format_vrchat_world_text(formatter, worlds, max_len=5000)

    def _build_rag_context(
        self,
        query: str,
        limit: int = 4,
        *,
        capability_only: bool = False,
        body_limit: int | None = 1200,
        channel_id: int | None = None,
    ) -> str:
        channel_knowledge = self._get_channel_knowledge(
            channel_id=channel_id,
            limit=4,
            max_chars=body_limit or 1200,
        )
        chunks = self._local_rag.retrieve(
            query,
            limit=limit,
            capability_only=capability_only,
            channel_id=channel_id,
        )
        blocks: list[str] = []
        if channel_knowledge:
            blocks.append(f"[このチャンネルの固定メモ]\n{channel_knowledge}")
        for chunk in chunks:
            body = chunk.body.strip()
            if body_limit is not None and len(body) > body_limit:
                body = body[:body_limit] + "\n...(省略)..."
            blocks.append(f"[{chunk.source} / {chunk.title}]\n{body}")
        return "\n\n".join(blocks)

    async def _send_chunked_text(
        self,
        channel: discord.abc.Messageable,
        text: str,
        *,
        prefix: str = "",
        chunk_size: int = 1900,
    ) -> None:
        remaining = (text or "").strip()
        if not remaining:
            return
        first = True
        while remaining:
            headroom = max(200, chunk_size - (len(prefix) if first and prefix else 0))
            if len(remaining) <= headroom:
                chunk = remaining
                remaining = ""
            else:
                split_at = remaining.rfind("\n", 0, headroom)
                if split_at < max(200, headroom // 2):
                    split_at = remaining.rfind(" ", 0, headroom)
                if split_at < max(200, headroom // 2):
                    split_at = headroom
                chunk = remaining[:split_at].rstrip()
                remaining = remaining[split_at:].lstrip()
            content = f"{prefix}{chunk}" if first and prefix else chunk
            await channel.send(content)
            first = False

    async def _answer_capability_query(
        self,
        channel: discord.abc.Messageable,
        query: str,
        mention: str | None = None,
        source_msg: discord.Message | None = None,
        *,
        channel_id: int | None = None,
    ) -> None:
        channel_id = int(channel_id or getattr(channel, "id", 0))
        if self._is_ai_channel_rate_limited(channel_id):
            prefix = f"{mention}\n" if mention else ""
            await channel.send(
                f"{prefix}このチャンネルではAI応答の間隔制限中です。数秒待ってから再実行してください。"
            )
            return
        progress_key = f"ai-progress:{channel_id}:capability:{mention or 'anon'}"
        if self._is_runtime_model_query(query):
            await self._send_runtime_model_reply(
                channel,
                mention=mention,
                source_msg=source_msg,
                input_text=query,
            )
            return

        normalized_query = (query or "").replace("きのう", "機能")
        channel_profile_block = self._build_channel_profile_block(
            channel_id=channel_id,
            limit=4,
            max_chars=1800,
        )
        rag_context = "\n\n".join(
            block
            for block in [
                self._build_command_catalog_context(),
                self._build_rag_context(
                    f"{normalized_query}\n機能一覧 できること 使い方 コマンド",
                    limit=12,
                    capability_only=True,
                    body_limit=None,
                    channel_id=channel_id,
                ),
                self._build_rag_context(
                    normalized_query,
                    limit=6,
                    capability_only=False,
                    body_limit=None,
                    channel_id=channel_id,
                ),
            ]
            if block
        )
        updates = (
            self._format_git_updates(count=4)
            if self._is_update_query(normalized_query)
            else ""
        )
        prompt = get_prompt("chat", "capability_prompt").format(
            channel_profile_block=channel_profile_block,
            query=normalized_query,
            rag_context=rag_context,
            updates_block=(f"[最新更新(git log)]\n{updates}\n" if updates else ""),
        )
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=channel,
                text_factory=lambda elapsed: self.bot.ai_progress_tracker.render(
                    ticket, elapsed
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            try:
                answer = await self._run_ollama_text(
                    model=model_name,
                    prompt=prompt,
                )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)
            answer = (
                strip_ansi_and_ctrl((answer or "").strip())
                or "関連資料から回答を作れませんでした。"
            )
            prefix = f"{mention}\n" if mention else ""
            await self._send_chunked_text(channel, answer, prefix=prefix)
            if source_msg is not None:
                await self._log_ai_output_event(
                    source_msg,
                    output_text=answer,
                    input_text=query,
                    title="AI 機能説明応答",
                    description="Bot の機能説明または更新情報へ応答しました。",
                )
        except Exception as e:
            prefix = f"{mention}\n" if mention else ""
            await send_event_log(
                self.bot,
                level="error",
                title="機能説明生成失敗",
                description="機能説明の AI 生成に失敗しました。",
                fields=[
                    ("チャンネル", str(getattr(channel, "id", 0)), True),
                    ("クエリ", query[:1000], False),
                    ("エラー", str(e)[:1000], False),
                ],
            )
            if isinstance(e, asyncio.TimeoutError):
                await channel.send(f"{prefix}モデル準備中です。完了したら通知します。")
                if mention:
                    asyncio.create_task(
                        self._notify_when_model_ready(
                            channel,
                            channel_id=getattr(channel, "id", 0),
                            user_id=0,
                            mention=mention,
                            model=self._cfg_str("ollama.model_default", "gpt-oss:120b"),
                        )
                    )
            else:
                await channel.send(
                    f"{prefix}機能説明の生成に失敗しました。\n```{str(e)[:180]}```"
                )
            if source_msg is not None:
                await self._log_ai_output_event(
                    source_msg,
                    level="error",
                    title="AI 機能説明応答失敗",
                    description="Bot の機能説明または更新情報の応答に失敗しました。",
                    input_text=query,
                    error_text=str(e),
                )
        finally:
            await self._ai_progress_countdowns.stop(progress_key, delete_message=True)

    def _bridge_targets(self, src: discord.TextChannel) -> list[discord.TextChannel]:
        targets: list[discord.TextChannel] = []
        for g in self.bot.guilds:
            me = g.me or (g.get_member(self.bot.user.id) if self.bot.user else None)
            for ch in g.text_channels:
                if ch.id == src.id or ch.name != "kenny-chat":
                    continue
                if me and ch.permissions_for(me).send_messages:
                    targets.append(ch)
        return targets

    async def _handle_kenny_chat_bridge(self, msg: discord.Message) -> bool:
        # クロスサーバーコラー生 成 成 を無効化（セキュリティのため）
        if not bool(_settings.get("kenny_chat.cross_server_bridge", False)):
            return False

        content = (msg.content or "").strip()
        if bool(_settings.get("kenny_chat.block_invite_and_mass_mention", True)):
            lowered = content.lower()
            if (
                "@everyone" in lowered
                or "@here" in lowered
                or "discord.gg/" in lowered
                or "discordapp.com/invite/" in lowered
            ):
                try:
                    await msg.delete()
                except Exception:
                    pass
                await msg.channel.send(
                    f"{msg.author.mention}\n`kenny-chat` では招待URL・@everyone/@here を禁止しています。",
                    delete_after=6,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return True

        # 12秒レート制限（ユーザー単位）
        now = time.time()
        last = self._kenny_chat_last_post.get(msg.author.id, 0.0)
        cooldown = float(self._cfg_int("kenny_chat.cooldown_seconds", 12))
        remain = cooldown - (now - last)
        if remain > 0:
            try:
                await msg.delete()
            except Exception:
                pass
            await msg.channel.send(
                f"{msg.author.mention}\n`kenny-chat` は {cooldown:.0f} 秒に 1 回までです。（あと {remain:.1f} 秒）",
                delete_after=5,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        self._kenny_chat_last_post[msg.author.id] = now

        body = self._collect_bridge_text(msg)
        if not body:
            return True

        initial = self._initial_of(msg.author)
        text = f"`{initial}` {body}"

        mirrors: list[tuple[int, int]] = []
        for target in self._bridge_targets(msg.channel):
            try:
                sent = await target.send(
                    text, allowed_mentions=discord.AllowedMentions.none()
                )
                mirrors.append((target.id, sent.id))
                self._kenny_chat_reverse[sent.id] = msg.id
            except Exception as e:
                logger.debug(f"kenny-chat bridge failed channel={target.id}: {e}")

        if mirrors:
            self._kenny_chat_mirrors[msg.id] = mirrors

        return True

    @commands.Cog.listener()
    async def on_message_delete(self, msg: discord.Message):
        """kenny-chat の元発言が削除されたら中継先も削除"""
        if msg.author.bot or not self._is_kenny_chat(msg):
            return

        mirrors = self._kenny_chat_mirrors.pop(msg.id, [])
        for ch_id, m_id in mirrors:
            ch = self.bot.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.get_partial_message(m_id).delete()
                except Exception:
                    pass
            self._kenny_chat_reverse.pop(m_id, None)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """キャッシュ外削除でも中継先を削除"""
        mirrors = self._kenny_chat_mirrors.pop(payload.message_id, [])
        for ch_id, m_id in mirrors:
            ch = self.bot.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.get_partial_message(m_id).delete()
                except Exception:
                    pass
            self._kenny_chat_reverse.pop(m_id, None)

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        """メッセージイベント（リアクション＆会話）"""
        # Bot自身のメッセージは無視
        if self.bot.user and msg.author.id == self.bot.user.id:
            return

        # DM は AI 会話のみ許可
        if msg.guild is None:
            if not msg.author.bot:
                await self._handle_dm_message(msg)
            return

        content = msg.content or ""

        # Bot は対象外（ウェブフック含む）
        is_webhook = msg.webhook_id is not None
        is_bot_account = msg.author.bot and not is_webhook
        if is_bot_account or is_webhook:
            return

        # 全メッセージ共通のスパム検出
        guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        if not guard.allow_message(msg.author.id, content):
            violation = guard.add_violation(msg.author.id, msg.guild.id)
            await self._handle_spam_violation(
                msg=msg,
                content=content,
                level=violation.current_level,
                violation_count=violation.violation_count,
            )
            await self.bot.process_commands(msg)
            return

        # kenny-chat は専用ルールで処理（クロスサーバー中継）
        if self._is_kenny_chat(msg):
            await self._handle_kenny_chat_bridge(msg)
            await self.bot.process_commands(msg)
            return

        # =========================
        # メンション / リプライ判定
        # =========================
        mentioned_bot = (
            any(member.id == self.bot.user.id for member in msg.mentions)
            if self.bot.user
            else False
        )
        is_reply_to_bot = (
            msg.reference
            and msg.reference.resolved
            and isinstance(msg.reference.resolved, discord.Message)
            and self.bot.user
            and msg.reference.resolved.author.id == self.bot.user.id
        )

        # メンション / リプライがない場合はリアクションのみ
        if not mentioned_bot and not is_reply_to_bot:
            # メッセージを履歴に記録
            user_name = msg.author.display_name or msg.author.name or str(msg.author.id)
            store = MessageStore(
                msg.guild.id,
                msg.channel.id,
                guild_name=msg.guild.name,
                channel_name=msg.channel.name,
            )
            store.add_message(user_name, content, msg.id, author_id=msg.author.id)
            self._schedule_message_index(
                guild_id=msg.guild.id,
                channel_id=msg.channel.id,
                message_id=msg.id,
                author_id=msg.author.id,
                author=user_name,
                content=content,
            )

            # キーワード -> 絵文字 の対応（config から取得）
            normalized_content = normalize_keyword_match_text(content)
            for keyword, emoji in self._cfg_map("keyword_reactions").items():
                if normalize_keyword_match_text(str(keyword)) in normalized_content:
                    try:
                        await msg.add_reaction(emoji)
                        await send_event_log(
                            self.bot,
                            guild=msg.guild,
                            level="info",
                            title="キーワードリアクション",
                            description=f"{msg.author.mention} のメッセージにリアクションを付与しました。",
                            fields=[
                                ("キーワード", keyword, True),
                                ("絵文字", emoji, True),
                                (
                                    "チャンネル",
                                    f"{msg.channel.name} ({msg.channel.id})",
                                    False,
                                ),
                                ("メッセージID", str(msg.id), True),
                            ],
                        )
                    except Exception as e:
                        logger.debug(f"Reaction failed: {e}")

            await self.bot.process_commands(msg)
            return

        # =========================
        # ここから AI 応答処理（メンション or リプライの場合）
        # =========================
        text = normalize_user_text(content)
        if not text:
            if mentioned_bot or is_reply_to_bot:
                await msg.channel.send(
                    f"{msg.author.mention}\nはい、どうしましたか？",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self.bot.process_commands(msg)
                return
            await self.bot.process_commands(msg)
            return
        text = self._sanitize_for_prompt(
            text,
            self._cfg_int("security.max_user_message_chars", 1200),
        )

        await self._log_ai_input_event(msg, text=text)

        lowered = text.lower()
        start_words = ("議事録開始", "議事録スタート", "minutes start", "start minutes")
        stop_words = ("議事録停止", "議事録終了", "minutes stop", "stop minutes")

        # メンション経由の議事録開始
        if any(w in lowered for w in start_words):
            if (
                not isinstance(msg.author, discord.Member)
                or not msg.author.voice
                or not isinstance(msg.author.voice.channel, discord.VoiceChannel)
            ):
                await msg.channel.send(
                    f"{msg.author.mention}\nVCに参加してから議事録を開始してください。"
                )
                await self.bot.process_commands(msg)
                return

            ok, info = await self.bot.meeting_minutes.start_session(  # type: ignore[attr-defined]
                bot=self.bot,
                guild=msg.guild,
                voice_channel=msg.author.voice.channel,
                started_by_id=msg.author.id,
                announce_channel_id=msg.channel.id
                if isinstance(
                    msg.channel,
                    (
                        discord.TextChannel,
                        discord.VoiceChannel,
                        discord.StageChannel,
                        discord.Thread,
                    ),
                )
                else None,
            )
            await msg.channel.send(f"{msg.author.mention}\n{info}")
            await self.bot.process_commands(msg)
            return

        # メンション経由の議事録停止
        if any(w in lowered for w in stop_words):
            result = await self.bot.meeting_minutes.stop_session(  # type: ignore[attr-defined]
                bot=self.bot,
                guild=msg.guild,
                reason=f"{msg.author.display_name} がメンションで手動停止",
                mention_user_id=msg.author.id,
            )
            if not result:
                await msg.channel.send(
                    f"{msg.author.mention}\n現在、進行中の議事録はありません。"
                )
                await self.bot.process_commands(msg)
                return

            embed = self.bot.meeting_minutes.build_result_embed(msg.guild, result)  # type: ignore[attr-defined]
            await msg.channel.send(content=msg.author.mention, embed=embed)
            await self.bot.process_commands(msg)
            return

        # 機能説明/最新更新の問い合わせはローカルRAG + git log を文脈に回答
        if self._is_runtime_model_query(text):
            await self._send_runtime_model_reply(
                msg.channel,
                mention=msg.author.mention,
                source_msg=msg,
                input_text=text,
            )
            await self.bot.process_commands(msg)
            return

        if self._is_channel_profile_query(text):
            await self._answer_channel_profile_query(
                msg.channel,
                text,
                mention=msg.author.mention,
                source_msg=msg,
                channel_id=msg.channel.id,
            )
            await self.bot.process_commands(msg)
            return

        if self._is_capability_query(text):
            await self._answer_capability_query(
                msg.channel,
                text,
                mention=msg.author.mention,
                source_msg=msg,
                channel_id=msg.channel.id,
            )
            await self.bot.process_commands(msg)
            return

        # ユーザー名を取得
        user = msg.author
        user_name = user.display_name or user.name or str(user.id)
        user_display, used_nickname = get_user_display_name(
            user.id, user_name, self._cfg_nicknames()
        )

        # スパム対策（AI 呼び出しレート制限）
        guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        if not guard.allow_ai(msg.author.id):
            remain = max(1, int(guard.ai_retry_after(msg.author.id)) + 1)
            if guard.should_warn(msg.author.id):
                await self._ai_retry_countdowns.start_or_replace(
                    key=f"ai-retry:{msg.channel.id}:{msg.author.id}",
                    channel=msg.channel,
                    initial_text=f"⏳ 残り {remain} 秒",
                    total_seconds=remain,
                    mention_user_id=msg.author.id,
                    done_text="✅ AI 呼び出しを再開できます。",
                )
            await self.bot.process_commands(msg)
            return
        if self._is_ai_channel_rate_limited(msg.channel.id):
            await msg.channel.send(
                f"{msg.author.mention}\nこのチャンネルではAI応答の間隔制限中です。数秒待ってから再実行してください。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self.bot.process_commands(msg)
            return

        # =========================
        # メッセージ履歴を保存・取得
        # =========================
        store = MessageStore(
            msg.guild.id,
            msg.channel.id,
            guild_name=msg.guild.name,
            channel_name=msg.channel.name,
        )
        store.add_message(user_name, text, msg.id, author_id=msg.author.id)
        self._schedule_message_index(
            guild_id=msg.guild.id,
            channel_id=msg.channel.id,
            message_id=msg.id,
            author_id=msg.author.id,
            author=user_name,
            content=text,
        )

        history_context = await self._resolve_chat_context(
            msg=msg,
            user_display=user_display,
            text=text,
        )
        if not history_context:
            history_lines = self._cfg_int("chat.history_lines", 100)
            history_text = store.get_recent_context(lines=history_lines)
            if history_text:
                history_context = HISTORY_CONTEXT_TEMPLATE.format(history=history_text)
            else:
                history_context = ""
        external_context = ""
        if self._live_info.needs_external_context(text):
            external_context = self._build_external_context_text(
                await asyncio.to_thread(self._live_info.build_context, text)
            )

        # =========================
        # プロンプトを生成（履歴と表示名を含める）
        # =========================
        prompt = PROMPT_TEMPLATE.format(
            user_display=user_display,
            history_context=history_context
            + (f"[外部参照情報]\n{external_context}\n\n" if external_context else ""),
            user_message=text,
            max_response_length_prompt=self._cfg_int(
                "chat.max_response_length_prompt", 500
            ),
        )
        today_local = datetime.now(JST)
        absolute_date = today_local.strftime("%Y-%m-%d")
        requires_current_lookup = is_current_info_intent(text)
        requires_bot_capability_grounding = self._is_bot_capability_or_game_query(text)
        progress_key = f"ai-progress:{msg.channel.id}:{msg.author.id}"
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        channel_profile_block = self._build_channel_profile_block(
            channel_id=msg.channel.id,
            limit=4,
            max_chars=1800,
        )
        ticket = await self.bot.ai_progress_tracker.create_ticket()

        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=msg.channel,
                mention_user_id=msg.author.id,
                text_factory=lambda elapsed: self.bot.ai_progress_tracker.render(
                    ticket, elapsed
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            try:
                async with msg.channel.typing():
                    tools: list[object] = []
                    if self.bot.ollama_client.has_web_tools():
                        tools = [
                            self._get_local_knowledge,
                            self._get_bot_game_catalog,
                            self._get_bot_command_catalog,
                            self._get_runtime_model_info,
                            self._search_vrchat_world,
                            self.bot.ollama_client.web_search,
                            self.bot.ollama_client.web_fetch,
                        ]
                    else:
                        tools = [
                            self._get_local_knowledge,
                            self._get_bot_game_catalog,
                            self._get_bot_command_catalog,
                            self._get_runtime_model_info,
                            self._search_vrchat_world,
                        ]
                    answer = await self._run_ollama_chat_with_tools(
                        model=model_name,
                        messages=[
                            {
                                "role": "system",
                                "content": get_prompt("chat", "system_message").format(
                                    absolute_date=absolute_date,
                                    channel_profile_block=channel_profile_block,
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    (
                                        f"[必須: 最新情報として扱う。回答に日付 {absolute_date} を明記すること]\n"
                                        if requires_current_lookup
                                        else ""
                                    )
                                    + (
                                        "[必須: これは Bot 自身の機能・ゲーム・コマンドに関する質問です。回答前に get_local_knowledge を使って確認し、資料にないことは断定しないこと]\n"
                                        if requires_bot_capability_grounding
                                        else ""
                                    )
                                    + prompt
                                ),
                            },
                        ],
                        tools=tools,
                        guild=msg.guild,
                        channel_id=msg.channel.id,
                        user_id=msg.author.id,
                    )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = (answer or "").strip()
            answer = strip_ansi_and_ctrl(answer)

            if not answer:
                answer = "(応答が空でした)"

            # 応答文字数制限（メンション部分を考慮：メンション約25文字 + 改行）
            max_len = self._cfg_int("chat.max_response_length", 1800)
            if len(answer) > max_len:
                answer = answer[:max_len] + "\n...(省略)..."

            # Bot の応答も履歴に保存
            bot_name = self.bot.user.name if self.bot.user else "Bot"
            bot_id = self.bot.user.id if self.bot.user else 0
            store.add_message(bot_name, answer, msg.id, author_id=bot_id)

            # メッセージ送信（メンションのみ）
            final_message = f"{msg.author.mention}\n{answer}"

            # 最終的なメッセージサイズが 2000 を超える場合は切り詰める
            if len(final_message) > 2000:
                # メンション+改行の長さを計算
                mention_overhead = len(f"{msg.author.mention}\n")
                max_answer_len = 2000 - mention_overhead - len("\n...(省略)...")
                answer = answer[:max_answer_len] + "\n...(省略)..."
                final_message = f"{msg.author.mention}\n{answer}"

            if self._should_send_letter_file(text):
                await self._send_letter_file(msg, answer)
            else:
                await msg.channel.send(
                    final_message, allowed_mentions=discord.AllowedMentions.none()
                )
            await self._log_ai_output_event(
                msg,
                output_text=answer,
                input_text=text,
                title="AI 応答成功",
                description="メンションまたはリプライへの AI 応答を送信しました。",
            )

        except Exception as e:
            logger.exception("AI response failed")
            await self._log_ai_output_event(
                msg,
                level="error",
                title="AI 応答失敗",
                description="メンションまたはリプライへの AI 応答に失敗しました。",
                input_text=text,
                error_text=str(e),
            )
            if isinstance(e, asyncio.TimeoutError):
                model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
                await msg.channel.send(
                    f"{msg.author.mention}\nモデル準備中です。完了したらメンションで通知します。",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                asyncio.create_task(
                    self._notify_when_model_ready(
                        msg.channel,
                        channel_id=msg.channel.id,
                        user_id=msg.author.id,
                        mention=msg.author.mention,
                        model=model_name,
                    )
                )
                await self.bot.process_commands(msg)
                return
            error_msg = str(e)

            # エラーメッセージを詳しく表示
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                detail = "Ollama 認証エラー: API キー設定を確認してください。"
            elif "prompt:latest" in str(e):
                detail = "モデル「prompt:latest」が見つかりません。ollama list で確認してください。"
            else:
                detail = f"詳細: {error_msg[:100]}"

            await msg.channel.send(
                f"{msg.author.mention}\n内部エラーが発生しました。\n```\n{detail}\n```",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            await self._ai_progress_countdowns.stop(progress_key, delete_message=True)

        # コマンド処理へ
        await self.bot.process_commands(msg)
