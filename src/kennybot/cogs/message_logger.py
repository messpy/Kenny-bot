# cogs/message_logger.py
# 会話 + リアクション

import base64
import json
import io
import logging
import re
import subprocess
import time
import asyncio
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import discord
from discord.ext import commands

from src.kennybot.utils.config import (
    PROMPT_TEMPLATE,
)
from src.kennybot.utils.message_store import MessageStore
from src.kennybot.utils.live_info import ExternalContext, LiveInfoService
from src.kennybot.utils.local_rag import LocalRAG
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.countdown import ChannelCountdown
from src.kennybot.utils.message_vector_store import MessageVectorStore
from src.kennybot.utils.command_catalog import COMMAND_CATEGORY_ORDER, HELP_SECTIONS, SLASH_COMMANDS
from src.kennybot.utils.paths import MESSAGE_VECTOR_DB_PATH
from src.kennybot.utils.message_logger import log_user_message, log_ai_output, log_system_event
from src.kennybot.cogs.base import BaseCog
from src.kennybot.utils.channel import resolve_log_channel
from src.kennybot.utils.text import (
    normalize_user_text,
    normalize_keyword_match_text,
    strip_ansi_and_ctrl,
)
from src.kennybot.utils.prompts import get_prompt
from src.kennybot.utils.vrchat_world import format_vrchat_world_text, search_vrchat_worlds
from src.kennybot.guards.spam_guard import SpamGuard
from src.kennybot.guards.mod_actions import ModActions


logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))
URL_RE = re.compile(r"https?://[^\s)>\"]+")
RAG_HEADER_RE = re.compile(r"^\[([^\]]+)\]")

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
        # (guild_id, channel_id, user_id) -> expires_at (monotonic seconds)
        self._recent_mention_windows: dict[tuple[int, int, int], float] = {}
        self._local_rag = LocalRAG(Path(__file__).resolve().parent.parent)
        self._live_info = LiveInfoService()
        self._model_ready_notifiers: set[tuple[int, int, str]] = set()
        self._vector_store = MessageVectorStore(MESSAGE_VECTOR_DB_PATH)
        self._ai_retry_countdowns = ChannelCountdown()
        self._ai_progress_countdowns = ChannelCountdown()

    def _prune_recent_mention_windows(self) -> None:
        now = time.monotonic()
        expired = [key for key, expires_at in self._recent_mention_windows.items() if expires_at <= now]
        for key in expired:
            self._recent_mention_windows.pop(key, None)

    def _arm_recent_mention_window(self, msg: discord.Message, *, seconds: int = 60) -> None:
        if msg.guild is None or seconds <= 0:
            return
        self._prune_recent_mention_windows()
        key = (msg.guild.id, msg.channel.id, msg.author.id)
        self._recent_mention_windows[key] = time.monotonic() + seconds

    def _has_recent_mention_window(self, msg: discord.Message) -> bool:
        if msg.guild is None:
            return False
        self._prune_recent_mention_windows()
        key = (msg.guild.id, msg.channel.id, msg.author.id)
        expires_at = self._recent_mention_windows.get(key)
        return bool(expires_at and expires_at > time.monotonic())

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

    def _merge_unique_strings(self, *collections: list[str]) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for collection in collections:
            for value in collection:
                item = str(value or "").strip()
                if not item or item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    def _query_terms(self, text: str) -> list[str]:
        normalized = normalize_keyword_match_text(text or "")
        raw_terms = re.split(r"[\s\u3000\W_]+", normalized)
        stop_words = {
            "の",
            "は",
            "が",
            "を",
            "に",
            "へ",
            "で",
            "と",
            "や",
            "か",
            "だ",
            "です",
            "ます",
            "今日",
            "きょう",
            "今",
            "いま",
            "最近",
            "最新",
            "何",
            "どれ",
            "ある",
            "いる",
        }
        terms: list[str] = []
        for term in raw_terms:
            item = term.strip()
            if not item or item in stop_words:
                continue
            if item not in terms:
                terms.append(item)
        return terms[:8]

    def _rank_web_items_for_query(
        self, query: str, items: list[object], *, max_items: int = 2
    ) -> list[object]:
        terms = self._query_terms(query)
        if not terms:
            return list(items[:max_items])
        scored: list[tuple[int, int, object]] = []
        for idx, item in enumerate(items):
            title = normalize_keyword_match_text(str(getattr(item, "title", "") or ""))
            snippet = normalize_keyword_match_text(str(getattr(item, "snippet", "") or ""))
            score = 0
            for term in terms:
                if term in title:
                    score += 3
                if term in snippet:
                    score += 1
            if score > 0:
                scored.append((score, idx, item))
        if scored:
            scored.sort(key=lambda x: (-x[0], x[1]))
            return [item for _, _, item in scored[:max_items]]
        return list(items[:max_items])

    def _strip_web_search_boilerplate(self, text: str) -> str:
        cleaned = strip_ansi_and_ctrl(text or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"^\[Web検索結果\]\s*", "", cleaned)
        cleaned = re.sub(
            r"^(Web検索結果を取得しました[。\.]?\s*)+",
            "",
            cleaned,
            flags=re.MULTILINE,
        )
        return cleaned.strip()

    def _looks_uncertain_answer(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(strip_ansi_and_ctrl(text or ""))
        markers = (
            "不明",
            "確認できません",
            "確認できていません",
            "わかりません",
            "わからない",
            "可能性があります",
            "と思われます",
            "と考えられます",
            "ようです",
            "みたいです",
            "かもしれません",
            "かもしれない",
        )
        return any(marker in normalized for marker in markers)

    def _has_web_references(self, references: list[str]) -> bool:
        return any(
            ref.startswith("tool:web_search")
            or ref.startswith("tool:web_fetch")
            or ref.startswith("source:web_search")
            or ref.startswith("method:")
            or ref.startswith("web_search")
            or ref.startswith("web_fetch")
            for ref in references
        )

    def _should_web_followup(self, answer: str, references: list[str]) -> bool:
        normalized = strip_ansi_and_ctrl(answer or "")
        return self._looks_uncertain_answer(normalized) and self._has_web_references(references)

    def _should_preemptive_web_followup(self, text: str) -> bool:
        return False

    def _needs_web_search_for_accuracy(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        keywords = (
            "天気",
            "気温",
            "温度",
            "weather",
            "ニュース",
            "news",
            "速報",
            "記事",
            "話題",
            "トレンド",
            "最新",
            "今日",
            "今",
            "現在",
            "株価",
            "為替",
        )
        return any(keyword in normalized for keyword in keywords)

    async def _promote_ai_progress_message(
        self,
        *,
        progress_key: str,
        ticket: str,
        model_name: str,
    ) -> None:
        message = self._ai_progress_countdowns.get_message(progress_key)
        if message is None:
            return
        try:
            await message.edit(
                content=self.bot.ai_progress_tracker.render(ticket, 1, model_name),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            logger.debug("Failed to promote AI progress message", exc_info=True)

    def _build_web_followup_prelude(self, user_display: str, mention: str, text: str) -> str:
        normalized = normalize_keyword_match_text(text or "")
        if any(word in normalized for word in ("天気", "気温", "温度", "weather")):
            intro = "天気を確認します。"
        elif any(word in normalized for word in ("ニュース", "news", "速報", "記事", "話題", "トレンド")):
            intro = "少し最新情報を確認します。"
        else:
            intro = "少し確認します。"
        return "\n".join(
            [
                f"{mention} こんにちは、{user_display}さん！",
                intro,
                "少し確認するので待ってください。",
            ]
        )

    async def _rewrite_answer_with_web(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[object],
        user_request: str,
        previous_answer: str,
        guild: discord.Guild | None = None,
        channel_id: int | None = None,
        user_id: int | None = None,
    ) -> tuple[str | None, list[str], list[str]]:
        retry_prompt = get_prompt("chat", "web_retry_prompt").format(
            user_request=user_request or "指定なし",
            previous_answer=previous_answer or "空",
        )
        retry_messages = [dict(item) for item in messages]
        insert_at = 1 if retry_messages and str(retry_messages[0].get("role") or "") == "system" else 0
        retry_messages.insert(
            insert_at,
            {
                "role": "system",
                "content": retry_prompt,
            },
        )
        return await self._run_ollama_chat_with_tools(
            model=model,
            messages=retry_messages,
            tools=tools,
            max_rounds=4,
            guild=guild,
            channel_id=channel_id,
            user_id=user_id,
        )

    async def _build_live_external_context(
        self, text: str
    ) -> tuple[str, list[str]]:
        if not self._live_info.needs_external_context(text):
            return "", []
        contexts = await asyncio.to_thread(self._live_info.build_context, text)
        if not contexts:
            return "", []
        body = self._build_external_context_text(contexts)
        refs = self._merge_unique_strings(
            [f"method:{item.label}" for item in contexts],
            self._extract_urls(body),
        )
        return body, refs

    async def _build_preemptive_web_context(
        self, text: str
    ) -> tuple[str, list[str], dict[str, str]]:
        if self._live_info.needs_external_context(text):
            body, refs = await self._build_live_external_context(text)
            if body:
                return body, refs, {}
        return "", [], {}

    def _format_web_reference_link(self, title: str, url: str) -> str:
        label = (title or "").strip() or url
        normalized_url = (url or "").strip().strip("<>").strip()
        return f"[{label}]({normalized_url})"

    def _build_web_reference_block(
        self, urls: list[str], title_map: dict[str, str] | None = None
    ) -> str:
        title_map = title_map or {}
        lines: list[str] = []
        for url in urls:
            label = title_map.get(url, url)
            lines.append(f"- {self._format_web_reference_link(label, url)}")
        return "\n".join(lines)

    def _parse_json_payload(self, raw: str) -> object | None:
        text = strip_ansi_and_ctrl(raw or "").strip()
        if not text:
            return None
        candidates = [text]
        for start, end in (("{", "}"), ("[", "]")):
            left = text.find(start)
            right = text.rfind(end)
            if left != -1 and right != -1 and right > left:
                candidates.append(text[left : right + 1].strip())
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue
        return None

    def _normalize_retrieval_plan(self, payload: object | None) -> list[dict[str, object]]:
        if payload is None:
            return []
        raw_items: list[object] = []
        if isinstance(payload, dict):
            plan = payload.get("plan")
            if isinstance(plan, list):
                raw_items = list(plan)
            else:
                sources = payload.get("sources")
                if isinstance(sources, list):
                    raw_items = [{"source": item} for item in sources]
        elif isinstance(payload, list):
            raw_items = list(payload)

        aliases = {
            "user_history": "recent_user_history",
            "history": "recent_turns",
            "conversation": "recent_turns",
            "channel": "channel_history",
            "profile": "channel_profile",
            "server_profile": "channel_profile",
            "getserverinfo": "channel_profile",
            "member_profile": "member_profile",
            "player_profile": "member_profile",
            "playerinfo": "member_profile",
            "getplayerinfo": "member_profile",
            "knowledge": "local_knowledge",
            "commands": "bot_command_catalog",
            "games": "bot_game_catalog",
            "model": "runtime_model",
            "world": "vrchat_world",
            "search": "web_search",
        }
        allowed = {
            "recent_user_history",
            "member_history",
            "recent_turns",
            "reply_chain",
            "channel_history",
            "semantic_history",
            "channel_profile",
            "member_profile",
            "local_knowledge",
            "bot_command_catalog",
            "bot_game_catalog",
            "runtime_model",
            "vrchat_world",
            "web_search",
            "none",
        }

        normalized: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()
        for item in raw_items:
            if isinstance(item, str):
                candidate: dict[str, object] = {"source": item}
            elif isinstance(item, dict):
                candidate = dict(item)
            else:
                continue
            source = str(candidate.get("source") or "").strip().lower()
            source = aliases.get(source, source)
            if not source or source not in allowed:
                continue
            if source == "web_search":
                continue
            if source == "none":
                continue
            candidate["source"] = source
            target = str(candidate.get("target") or "").strip().lower()
            if target:
                candidate["target"] = target
            query = str(candidate.get("query") or "").strip()
            if query:
                candidate["query"] = query
            web_scope = str(candidate.get("web_scope") or "").strip().lower()
            if web_scope:
                candidate["web_scope"] = web_scope
            limit = candidate.get("limit")
            if isinstance(limit, str) and limit.isdigit():
                candidate["limit"] = int(limit)
            elif isinstance(limit, (int, float)):
                candidate["limit"] = int(limit)
            capability_only = candidate.get("capability_only")
            if isinstance(capability_only, str):
                candidate["capability_only"] = capability_only.lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
            elif isinstance(capability_only, bool):
                candidate["capability_only"] = capability_only
            key = (
                source,
                candidate.get("target", ""),
                candidate.get("query", ""),
                candidate.get("limit", ""),
                candidate.get("web_scope", ""),
                candidate.get("capability_only", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
            if len(normalized) >= 8:
                break
        return normalized

    def _preferred_person_target_key(
        self, target_candidates: dict[str, tuple[int, str]]
    ) -> str | None:
        for key in target_candidates.keys():
            if key.startswith("mentioned_"):
                return key
        if "replied_user" in target_candidates:
            return "replied_user"
        return None

    def _prefer_explicit_person_target_plan(
        self,
        *,
        plan: list[dict[str, object]],
        text: str,
        target_candidates: dict[str, tuple[int, str]],
        has_reply_chain: bool,
        user_lines: int,
    ) -> list[dict[str, object]]:
        if not plan or not self._is_person_lookup_query(text):
            return plan
        preferred_target = self._preferred_person_target_key(target_candidates)
        if not preferred_target:
            return plan

        forced_prefix: list[dict[str, object]] = []
        if has_reply_chain and not any(
            str(item.get("source") or "").strip().lower() == "reply_chain"
            for item in plan
        ):
            forced_prefix.append(
                {
                    "source": "reply_chain",
                    "limit": min(max(4, user_lines // 2), 8),
                }
            )
        if not any(
            str(item.get("source") or "").strip().lower() == "member_profile"
            and str(item.get("target") or "").strip().lower() == preferred_target
            for item in plan
        ):
            forced_prefix.append(
                {
                    "source": "member_profile",
                    "target": preferred_target,
                }
            )
        if not any(
            str(item.get("source") or "").strip().lower() == "member_history"
            and str(item.get("target") or "").strip().lower() == preferred_target
            for item in plan
        ):
            forced_prefix.append(
                {
                    "source": "member_history",
                    "target": preferred_target,
                    "limit": min(max(user_lines, 6), 24),
                }
            )

        adjusted: list[dict[str, object]] = []
        for item in plan:
            candidate = dict(item)
            source = str(candidate.get("source") or "").strip().lower()
            target = str(candidate.get("target") or "author").strip().lower()
            if source == "recent_user_history":
                candidate["source"] = "member_history"
                candidate["target"] = preferred_target
                candidate["limit"] = min(max(user_lines, 6), 24)
            if source in {"member_history", "member_profile"}:
                if target == "author":
                    candidate["target"] = preferred_target
            adjusted.append(candidate)

        adjusted = forced_prefix + adjusted

        unique: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()
        for item in adjusted:
            key = (
                str(item.get("source") or "").strip().lower(),
                str(item.get("target") or "").strip().lower(),
                str(item.get("query") or "").strip(),
                item.get("limit", ""),
                str(item.get("web_scope") or "").strip().lower(),
                bool(item.get("capability_only", False)),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= 8:
                break
        return unique

    @staticmethod
    def _format_target_candidates(
        target_candidates: dict[str, tuple[int, str]]
    ) -> dict[str, dict[str, object]]:
        return {
            key: {"user_id": user_id, "display": display}
            for key, (user_id, display) in target_candidates.items()
        }

    def _fallback_retrieval_plan(
        self,
        *,
        text: str,
        user_lines: int,
        channel_lines: int,
        has_profile: bool,
    ) -> list[dict[str, object]]:
        normalized = normalize_keyword_match_text(text or "")
        plan: list[dict[str, object]] = []
        if self._is_channel_profile_query(text):
            if has_profile:
                plan.append({"source": "channel_profile"})
            plan.append({"source": "recent_turns", "limit": min(max(channel_lines, 4), 8)})
            return plan
        if self._is_local_activity_query(text):
            plan.append(
                {
                    "source": "recent_user_history",
                    "target": "author",
                    "limit": min(max(user_lines, 6), 24),
                }
            )
            if "返信" in normalized or "リプ" in normalized:
                plan.append({"source": "reply_chain", "limit": 4})
            return plan
        if has_profile:
            plan.append({"source": "channel_profile"})
        plan.append({"source": "recent_turns", "limit": min(max(channel_lines, 4), 8)})
        if self._needs_web_search_for_accuracy(text):
            plan.insert(
                0,
                {
                    "source": "web_search",
                    "query": text,
                    "web_scope": "auto",
                },
            )
        return plan

    async def _build_retrieval_plan(
        self,
        *,
        msg: discord.Message,
        user_display: str,
        text: str,
        channel_profile_available: bool,
    ) -> list[dict[str, object]]:
        guild_id = msg.guild.id if msg.guild else 0
        channel_id = msg.channel.id
        guild_name = msg.guild.name if msg.guild else "DM"
        channel_name = (
            msg.channel.name if hasattr(msg.channel, "name") else str(msg.channel.id)
        )
        user_lines = self._cfg_int("chat.user_history_lines", 24)
        channel_lines = self._cfg_int("chat.channel_history_lines", 16)
        target_candidates = self._context_target_candidates(msg)
        prompt = get_prompt("chat", "retrieval_plan_prompt").format(
            user_id=msg.author.id,
            user_display=user_display,
            guild_id=guild_id,
            guild_name=guild_name,
            channel_id=channel_id,
            channel_name=channel_name,
            message=text,
            user_history_limit=user_lines,
            channel_history_limit=channel_lines,
            channel_profile_available=str(bool(channel_profile_available)).lower(),
            available_targets=json.dumps(
                {
                    key: {"user_id": value[0], "display": value[1]}
                    for key, value in target_candidates.items()
                },
                ensure_ascii=False,
            ),
            explicit_mention_targets=json.dumps(
                [
                    key
                    for key in target_candidates.keys()
                    if key.startswith("mentioned_")
                ],
                ensure_ascii=False,
            ),
        )
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                    format="json",
                ),
                timeout=min(20, max(8, self._cfg_int("ollama.timeout_sec", 180))),
            )
            plan = self._normalize_retrieval_plan(self._parse_json_payload(raw or ""))
            if plan:
                return plan
        except Exception:
            logger.exception("Failed to build retrieval plan via AI")
        return self._fallback_retrieval_plan(
            text=text,
            user_lines=user_lines,
            channel_lines=channel_lines,
            has_profile=bool(channel_profile_available),
        )

    async def _build_current_info_context(
        self,
        text: str,
        *,
        web_scope: str = "auto",
    ) -> tuple[str, list[str], dict[str, str], list[str]]:
        def _looks_like_failure(result: tuple[str, list[str], dict[str, str], list[str]]) -> bool:
            body = strip_ansi_and_ctrl(str(result[0] or "")).strip()
            if not body:
                return True
            normalized = normalize_keyword_match_text(body)
            failure_markers = (
                "取得失敗",
                "検索結果が取得できませんでした",
                "web検索の実行に失敗しました",
                "最新情報の検索に失敗しました",
                "見つかりませんでした",
            )
            return any(marker in normalized for marker in failure_markers)

        last_result: tuple[str, list[str], dict[str, str], list[str]] = ("", [], {}, [])
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(0.75 * attempt)
            result = await self._build_current_info_context_once(
                text,
                web_scope=web_scope,
            )
            if not _looks_like_failure(result):
                return result
            last_result = result
        return last_result

    async def _build_current_info_context_once(
        self,
        text: str,
        *,
        web_scope: str = "auto",
    ) -> tuple[str, list[str], dict[str, str], list[str]]:
        if self._live_info.needs_external_context(text):
            contexts = await asyncio.to_thread(self._live_info.build_context, text)
            if contexts:
                source_urls = {
                    "天気API": "https://open-meteo.com/",
                    "日付・祝日API": "https://date.nager.at/",
                }
                refs: list[str] = []
                title_map: dict[str, str] = {}
                queries: list[str] = []
                for item in contexts:
                    refs.append(f"method:{item.label}")
                    url = source_urls.get(item.label)
                    if url:
                        refs.append(url)
                        title_map[url] = item.label
                    queries.append(item.label)
                body = self._build_external_context_text(contexts)
                return body, refs, title_map, queries

        search_service = getattr(self.bot, "ai_search", None)
        if search_service is None:
            return "", [], {}, []
        scope = normalize_keyword_match_text(web_scope or "").strip().lower()
        news_only: bool | None = None
        if scope in {"news", "latest"}:
            news_only = True
        elif scope in {"web", "general"}:
            news_only = False
        try:
            result = await asyncio.wait_for(
                search_service.answer_ai_async(
                    text,
                    mode="normal",
                    news_only=news_only,
                ),
                timeout=max(20, self._cfg_int("ollama.timeout_sec", 180)),
            )
        except Exception:
            logger.exception("AI search context build failed")
            searcher = getattr(search_service, "searcher", None)
            if searcher is None or not callable(getattr(searcher, "search", None)):
                return "", [], {}, []
            try:
                lowered = normalize_keyword_match_text(text or "")
                prefer_web = any(k in lowered for k in ("意味", "とは", "定義", "由来", "語源"))
                if news_only is True:
                    prefer_web = False
                elif news_only is False:
                    prefer_web = True
                items = await asyncio.to_thread(
                    searcher.search,
                    text,
                    news_only=not prefer_web,
                )
            except Exception:
                logger.exception("Direct DDGS fallback search failed")
                return "", [], {}, []
            if not items:
                return "", [], {}, []
            ranked_items = self._rank_web_items_for_query(text, list(items), max_items=2)
            refs = ["method:ddgs.search"]
            urls = self._merge_unique_strings(
                [item.url for item in ranked_items if getattr(item, "url", "").strip()]
            )[:2]
            refs.extend(urls)
            title_map = {
                item.url: item.title
                for item in ranked_items[:2]
                if getattr(item, "url", "").strip()
            }
            queries = [text.strip()] if text.strip() else []
            lines: list[str] = []
            lines.append("全体要約")
            for item in ranked_items[:2]:
                date_str = f" ({item.date})" if item.date else ""
                snippet = f"\n{item.snippet.strip()}" if item.snippet.strip() else ""
                lines.append(f"- {item.title}{date_str}\n  {item.url}{snippet}")
            return "\n".join(lines), refs, title_map, queries

        refs: list[str] = ["method:ai_search.answer_ai_async", "method:ddgs.search"]
        ranked_items = self._rank_web_items_for_query(text, list(result.items), max_items=2)
        urls = [item.url for item in ranked_items if getattr(item, "url", "").strip()]
        urls = self._merge_unique_strings(urls)[:2]
        for url in urls:
            refs.append(url)
        title_map = {
            item.url: item.title
            for item in ranked_items[:2]
            if getattr(item, "url", "").strip()
        }
        queries = [result.query] + [q for q in getattr(result, "searched_queries", []) if q]

        if ranked_items:
            item_lines: list[str] = []
            for item in ranked_items[:2]:
                date_str = f" ({item.date})" if item.date else ""
                snippet = f"\n{item.snippet.strip()}" if item.snippet.strip() else ""
                item_lines.append(f"- {item.title}{date_str}\n  {item.url}{snippet}")
            if item_lines:
                return "\n".join(item_lines).strip(), refs, title_map, queries
        answer = (result.answer or "").strip()
        if answer and not ranked_items:
            return self._strip_web_search_boilerplate(answer), refs, title_map, queries
        return "", refs, title_map, queries

    async def _handle_current_info_search_failure(
        self,
        channel: discord.abc.Messageable,
        *,
        mention: str | None = None,
        query: str = "",
        source_msg: discord.Message | None = None,
        model_name: str = "",
        references: list[str] | None = None,
    ) -> None:
        prefix = f"{mention}\n" if mention else ""
        await channel.send(
            f"{prefix}最新情報の検索に失敗しました。少し待ってからもう一度試してください。"
        )
        if source_msg is not None:
            await self._log_bot_activity_event(
                source_msg,
                kind="メンション",
                processing="最新情報検索",
                level="warning",
                title="Bot 会話ログ",
                description="最新情報検索に失敗しました。",
                input_text=query,
                output_text="最新情報の検索に失敗しました。",
                model_name=model_name,
                references=references or [],
            )

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

    @staticmethod
    def _format_profile_dt(value: object) -> str:
        if not value:
            return "不明"
        try:
            if isinstance(value, datetime):
                return value.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        except Exception:
            pass
        return str(value)

    def _format_member_profile(self, member: discord.Member) -> str:
        display_name = getattr(member, "display_name", None) or member.name or str(member.id)
        nick = getattr(member, "nick", None) or "なし"
        role_names = [
            role.name
            for role in getattr(member, "roles", [])
            if getattr(role, "name", "@everyone") != "@everyone"
        ]
        if len(role_names) > 10:
            role_names = role_names[:10] + [f"...他{len(role_names) - 10}件"]
        activities = []
        for act in getattr(member, "activities", []) or []:
            act_name = getattr(act, "name", "") or str(act)
            act_type = getattr(getattr(act, "type", None), "name", None) or getattr(act, "type", None)
            if act_type:
                activities.append(f"{act_name} ({act_type})")
            else:
                activities.append(act_name)
        if len(activities) > 5:
            activities = activities[:5] + [f"...他{len(activities) - 5}件"]
        status = getattr(member, "status", None)
        status_text = str(status) if status is not None else "不明"
        lines = [
            "[メンバープロフィール]",
            f"対象: {display_name} ({member.id})",
            f"ユーザー名: {member.name}",
            f"ニックネーム: {nick}",
            f"表示名: {display_name}",
            f"アカウント作成日: {self._format_profile_dt(getattr(member, 'created_at', None))}",
            f"サーバー参加日時: {self._format_profile_dt(getattr(member, 'joined_at', None))}",
            f"ブースト開始日時: {self._format_profile_dt(getattr(member, 'premium_since', None))}",
            f"ロール一覧: {', '.join(role_names) if role_names else 'なし'}",
            f"オンライン状態: {status_text}",
            f"アクティビティ: {', '.join(activities) if activities else 'なし'}",
        ]
        return "\n".join(lines)

    async def _resolve_chat_context(
        self,
        *,
        msg: discord.Message,
        user_display: str,
        text: str,
    ) -> tuple[str, list[str], list[str]]:
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
                guild_id=guild_id,
                channel_id=channel_id,
            )

        async def get_member_profile(target: str = "author") -> str:
            """Get the target member's profile-style metadata."""
            target_key = (target or "author").strip().lower()
            target_info = target_candidates.get(target_key) or target_candidates["author"]
            member: discord.Member | None = None
            if msg.guild is not None:
                member = msg.guild.get_member(target_info[0])
                if member is None:
                    try:
                        member = await msg.guild.fetch_member(target_info[0])
                    except Exception:
                        member = None
            if member is None and isinstance(msg.author, discord.Member) and target_info[0] == msg.author.id:
                member = msg.author
            if member is None:
                return ""
            return self._format_member_profile(member)

        channel_profile_block = self._build_channel_profile_block(
            channel=msg.channel,
            channel_id=channel_id,
            guild_id=guild_id,
            limit=4,
            max_chars=1800,
        )
        plan = await self._build_retrieval_plan(
            msg=msg,
            user_display=user_display,
            text=text,
            channel_profile_available=bool(channel_profile_block),
        )
        plan = self._prefer_explicit_person_target_plan(
            plan=plan,
            text=text,
            target_candidates=target_candidates,
            has_reply_chain=bool(msg.reference and msg.reference.resolved),
            user_lines=user_lines,
        )
        if self._needs_web_search_for_accuracy(text) and not any(
            str(item.get("source") or "").strip().lower() == "web_search"
            for item in plan
        ):
            plan.insert(
                0,
                {
                    "source": "web_search",
                    "query": text,
                    "web_scope": "auto",
                },
            )

        blocks: list[tuple[str, str]] = []
        references: list[str] = []
        web_queries: list[str] = []
        used_sources: list[str] = []
        preferred_mention_target = self._preferred_person_target_key(target_candidates)
        prefer_mentioned_targets = bool(preferred_mention_target) and bool(msg.mentions)
        person_focus_block = ""
        if preferred_mention_target and (msg.mentions or "replied_user" in target_candidates):
            focus_lines = [
                "[この会話で明示された人物候補]",
            ]
            if msg.mentions:
                for key, (member_id, display_name) in target_candidates.items():
                    if not key.startswith("mentioned_"):
                        continue
                    focus_lines.append(f"- {key}: {display_name} ({member_id})")
                focus_lines.append(
                    "この質問に人物が関わるなら、上の mention 候補を author より優先して解釈すること。"
                )
            elif "replied_user" in target_candidates:
                member_id, display_name = target_candidates["replied_user"]
                focus_lines.append(f"- replied_user: {display_name} ({member_id})")
                focus_lines.append(
                    "返信が基準なら replied_user を author より優先して解釈すること。"
                )
            person_focus_block = "\n".join(focus_lines) + "\n\n"

        for item in plan:
            source = str(item.get("source") or "").strip().lower()
            target = str(item.get("target") or "author").strip().lower()
            query = str(item.get("query") or text or "").strip()
            limit = item.get("limit")
            capability_only = bool(item.get("capability_only", False))
            web_scope = str(item.get("web_scope") or "auto").strip().lower()
            body = ""
            title = ""

            if (
                prefer_mentioned_targets
                and source in {"member_history", "member_profile"}
                and target == "author"
            ):
                target = preferred_mention_target or target
            if (
                source == "recent_user_history"
                and self._is_person_lookup_query(text)
                and preferred_mention_target
            ):
                source = "member_history"
                target = preferred_mention_target
            if source == "reply_chain" and msg.reference and msg.reference.resolved:
                body = get_reply_chain(int(limit) if isinstance(limit, int) else 4)
                title = "直前の会話チェーン"
                if body:
                    blocks.append((title, body))
                    references.extend(self._collect_reference_labels(body))
                    if source not in used_sources:
                        used_sources.append(source)
                continue

            if source == "recent_user_history":
                lines = int(limit) if isinstance(limit, int) else user_lines
                body = get_user_history(lines)
                title = f"このユーザーの最近の発言 {lines} 件以内"
            elif source == "member_history":
                lines = int(limit) if isinstance(limit, int) else user_lines
                body = get_member_history(target=target, lines=lines)
                target_info = (
                    target_candidates.get(target) or target_candidates["author"]
                )
                title = f"{target_info[1]} の最近の発言 {lines} 件以内"
            elif source == "member_profile":
                body = await get_member_profile(target=target)
                target_info = (
                    target_candidates.get(target) or target_candidates["author"]
                )
                title = f"{target_info[1]} のプロフィール"
            elif source == "recent_turns":
                lines = int(limit) if isinstance(limit, int) else 6
                body = get_recent_turns(lines)
                title = "このチャンネルの直近会話"
            elif source == "reply_chain":
                lines = int(limit) if isinstance(limit, int) else 4
                body = get_reply_chain(lines)
                title = "直前の会話チェーン"
            elif source == "channel_history":
                lines = int(limit) if isinstance(limit, int) else channel_lines
                body = get_channel_history(lines)
                title = f"このチャンネル全体の最近の発言 {lines} 件以内"
            elif source == "semantic_history":
                query_embedding = await self._embed_text(query)
                if query_embedding:
                    scope_value = str(item.get("scope") or "channel").strip().lower()
                    limit_value = max(
                        1,
                        min(
                            int(
                                limit
                                if isinstance(limit, int)
                                else self._cfg_int("chat.semantic_history_k", 6)
                            ),
                            12,
                        ),
                    )
                    target_info = (
                        target_candidates.get(target) or target_candidates["author"]
                    )
                    rows = await asyncio.to_thread(
                        self._vector_store.semantic_search,
                        guild_id=guild_id,
                        channel_id=channel_id,
                        query_embedding=query_embedding,
                        author_id=target_info[0] if scope_value == "user" else None,
                        limit=limit_value,
                    )
                    body = self._vector_store.format_results(rows)
                    if body:
                        title = (
                            f"{target_info[1]} の意味的に近い過去発言"
                            if scope_value == "user"
                            else "このチャンネルの意味的に近い過去発言"
                        )
            elif source == "channel_profile":
                body = channel_profile_block
                title = "この場所の正式プロフィール"
            elif source == "local_knowledge":
                body = get_local_knowledge(
                    query=query,
                    limit=int(limit) if isinstance(limit, int) else 4,
                    capability_only=capability_only,
                )
                title = "Bot ローカル資料"
            elif source == "bot_command_catalog":
                body = self._get_bot_command_catalog(str(item.get("category") or ""))
                title = "Bot コマンド一覧"
            elif source == "bot_game_catalog":
                body = self._get_bot_game_catalog()
                title = "Bot ゲーム一覧"
            elif source == "runtime_model":
                body = self._get_runtime_model_info()
                title = "現在のモデル設定"
            elif source == "vrchat_world":
                body = self._search_vrchat_world(
                    keyword=query or text or "",
                    count=int(limit) if isinstance(limit, int) else 5,
                    author=str(item.get("author") or ""),
                    tag=str(item.get("tag") or ""),
                )
                title = "VRChat ワールド検索結果"
            elif source == "web_search":
                body, web_refs, web_titles, search_queries = await self._build_current_info_context(
                    query or text or "",
                    web_scope=web_scope,
                )
                references.extend(web_refs)
                title = "検索結果の要約"
                web_queries.extend([q for q in search_queries if q])
            else:
                continue

            if body:
                blocks.append((title or source, body))
                references.extend(self._collect_reference_labels(body))
                if source not in used_sources:
                    used_sources.append(source)

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
                    references.extend(self._collect_reference_labels(body))

        should_attach_channel_profile = self._is_channel_profile_query(text) or any(
            item.get("source") == "channel_profile" for item in plan
        )
        if should_attach_channel_profile and channel_profile_block and not any(
            title == "この場所の正式プロフィール" for title, _ in blocks
        ):
            blocks.insert(0, ("この場所の正式プロフィール", channel_profile_block))
            references.extend(self._collect_reference_labels(channel_profile_block))
            if "channel_profile" not in used_sources:
                used_sources.append("channel_profile")

        if self._is_person_lookup_query(text) or bool(preferred_mention_target):
            try:
                log_system_event(
                    "AI コンテキスト選択",
                    msg=msg,
                    level="info",
                    details={
                        "preferred_target": preferred_mention_target,
                        "has_reply_chain": bool(msg.reference and msg.reference.resolved),
                        "person_lookup": self._is_person_lookup_query(text),
                        "plan_sources": [
                            str(item.get("source") or "").strip().lower()
                            for item in plan
                        ],
                        "used_sources": used_sources,
                        "target_candidates": self._format_target_candidates(
                            target_candidates
                        ),
                    },
                )
            except Exception:
                logger.debug("Failed to log AI context selection", exc_info=True)

        for source in used_sources:
            source_ref = f"source:{source}"
            if source_ref not in references:
                references.append(source_ref)

        return self._build_history_context(blocks), self._merge_unique_strings(references), self._merge_unique_strings(web_queries)

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
    ) -> tuple[str | None, list[str], list[str]]:
        if not tools:
            response = await asyncio.to_thread(
                self.bot.ollama_client.chat,
                model=model,
                messages=messages,
                stream=False,
            )
            return self._extract_message_content(response), [], []

        working_messages = [dict(item) for item in messages]
        source_urls: list[str] = []
        used_tools: list[str] = []
        last_tool_outputs: list[tuple[str, str]] = []
        web_queries: list[str] = []
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
                references = [f"tool:{name}" for name in used_tools]
                references.extend(source_urls)
                return answer, references, self._merge_unique_strings(web_queries)

            async def execute_tool_call(call: object) -> tuple[dict, list[str]]:
                name, args = self._normalize_tool_call(call)
                if name and name not in used_tools:
                    used_tools.append(name)
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
                        query_text = str(args.get("query") or args.get("url") or "").strip()
                        if query_text:
                            web_queries.append(query_text)
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
                        found_urls = self._extract_urls(result_text)[:3]
                except Exception as e:
                    logger.exception("Tool call failed: %s", name)
                    result_text = f"Tool {name} failed: {e}"
                    found_urls = []
                if len(result_text) > 8000:
                    result_text = result_text[:8000] + "\n...(省略)..."
                if name in {"web_search", "web_fetch"}:
                    result_text = self._strip_web_search_boilerplate(result_text)
                    if not result_text:
                        result_text = "検索結果の本文は取得できませんでした。"
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

        references = [f"tool:{name}" for name in used_tools]
        references.extend(source_urls)
        return answer, references, self._merge_unique_strings(web_queries)

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
        t = re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "").lower()
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
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
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
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
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
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
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

    def _is_local_activity_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
        keywords = (
            "最近の行動",
            "最近の発言",
            "最近の投稿",
            "最近何して",
            "最近どう",
            "履歴",
            "発言履歴",
            "行動履歴",
            "活動履歴",
            "このユーザー",
            "この人",
            "この人の",
            "このメンバー",
            "この子",
        )
        return any(keyword in normalized for keyword in keywords)

    def _is_person_lookup_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
        keywords = (
            "どんな人",
            "どんなやつ",
            "どんな子",
            "最後の投稿",
            "最後の発言",
            "最後に投稿",
            "最後に発言",
            "最新の投稿",
            "最新の発言",
            "最近の投稿",
            "最近の発言",
            "プロフィール",
            "情報",
            "何者",
            "誰",
            "投稿ある",
            "発言ある",
            "性格",
            "特徴",
            "教えて",
            "紹介",
        )
        return any(keyword in normalized for keyword in keywords)

    def _sanitize_for_prompt(self, text: str, max_len: int) -> str:
        v = strip_ansi_and_ctrl(text or "")
        v = v.replace("@everyone", "＠everyone").replace("@here", "＠here")
        if max_len > 0 and len(v) > max_len:
            return v[:max_len]
        return v

    def _looks_like_non_natural_language(self, text: str) -> bool:
        raw = strip_ansi_and_ctrl(text or "").strip()
        if not raw:
            return False

        if "```" in raw:
            return True

        long_tokens = re.findall(r"[A-Za-z0-9+/=_-]{16,}", raw)
        if long_tokens:
            return True

        japanese_chars = 0
        latin_chars = 0
        symbol_chars = 0
        for ch in raw:
            if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff":
                japanese_chars += 1
            elif ch.isascii() and ch.isalpha():
                latin_chars += 1
            category = unicodedata.category(ch)
            if category.startswith(("P", "S")):
                symbol_chars += 1

        length = len(raw)
        if length >= 16 and japanese_chars == 0:
            ratio = (latin_chars + symbol_chars) / max(length, 1)
            if ratio > 0.7:
                return True

        if japanese_chars == 0 and " " not in raw and length >= 12:
            if re.fullmatch(r"[A-Za-z0-9._+\-=/]+", raw):
                return True

        return False

    def _decode_obfuscated_text(self, text: str) -> tuple[str | None, str | None]:
        raw = strip_ansi_and_ctrl(text or "").strip()
        if not raw:
            return None, None

        # まず URL エンコード系を試す
        if "%" in raw:
            unquoted = unquote(raw).strip()
            if unquoted and unquoted != raw:
                return unquoted, "url"

        compact = re.sub(r"\s+", "", raw)
        if len(compact) < 12:
            return None, None

        if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
            return None, None

        for candidate in (compact, compact.replace("-", "+").replace("_", "/")):
            padded = candidate + "=" * (-len(candidate) % 4)
            try:
                decoded = base64.b64decode(padded, validate=True)
            except Exception:
                continue
            try:
                text = decoded.decode("utf-8").strip()
            except Exception:
                continue
            if text:
                return text, "base64"

        return None, None

    def _looks_like_disallowed_post_conversion(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        if any(
            keyword in normalized
            for keyword in (
                "初期設定",
                "内部設定",
                "隠し設定",
                "秘密",
                "instructions",
                "developer message",
                "developerprompt",
                "hidden prompt",
                "systemmessage",
            )
        ):
            return True

        prompt_patterns = (
            r"(?:プロンプト|prompt|system prompt|systemprompt|developer prompt|developerprompt).{0,8}"
            r"(?:教えて|おしえて|見せて|開示|表示|晒|晒して|教えろ|出して)",
            r"(?:教えて|おしえて|見せて|開示|表示|晒|晒して|教えろ|出して).{0,8}"
            r"(?:プロンプト|prompt|system prompt|systemprompt|developer prompt|developerprompt)",
        )
        return any(re.search(pattern, normalized) for pattern in prompt_patterns)

    async def _send_natural_language_only_reply(
        self,
        channel: discord.abc.Messageable,
        *,
        msg: discord.Message,
        text: str,
        source: str,
    ) -> None:
        await channel.send(
            f"{msg.author.mention}\n自然言語で聞いてください。"
            "コード、エンコード文字列、記号だけの入力には答えません。",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await asyncio.to_thread(
                log_system_event,
                "自然言語以外を拒否",
                msg=msg,
                level="warning",
                description="非自然言語の入力を検出したため応答を拒否しました。",
                details={
                    "source": source,
                    "input_preview": text[:120],
                },
            )
        except Exception:
            logger.debug("Failed to log natural language rejection", exc_info=True)

    async def _prepare_user_text_for_ai(
        self,
        text: str,
        *,
        max_len: int,
        source: str,
        msg: discord.Message,
        channel: discord.abc.Messageable,
    ) -> str | None:
        sanitized = self._sanitize_for_prompt(text, max_len)
        if not sanitized:
            return None

        if not self._looks_like_non_natural_language(sanitized):
            return sanitized

        decoded_text, decode_source = self._decode_obfuscated_text(sanitized)
        if not decoded_text:
            await self._send_natural_language_only_reply(
                channel,
                msg=msg,
                text=sanitized,
                source=source,
            )
            return None

        decoded_text = self._sanitize_for_prompt(decoded_text, max_len)
        if not decoded_text or self._looks_like_non_natural_language(decoded_text):
            await self._send_natural_language_only_reply(
                channel,
                msg=msg,
                text=decoded_text or sanitized,
                source=f"{source}:{decode_source or 'decoded'}",
            )
            return None

        if self._looks_like_disallowed_post_conversion(decoded_text):
            await self._send_natural_language_only_reply(
                channel,
                msg=msg,
                text=decoded_text,
                source=f"{source}:{decode_source or 'decoded'}",
            )
            return None

        try:
            await asyncio.to_thread(
                log_system_event,
                "入力を変換",
                msg=msg,
                level="info",
                description="非自然言語の入力をデコードして自然文として扱いました。",
                details={
                    "source": source,
                    "decode_source": decode_source or "unknown",
                    "input_preview": sanitized[:120],
                    "converted_preview": decoded_text[:120],
                },
            )
        except Exception:
            logger.debug("Failed to log input conversion", exc_info=True)

        return decoded_text

    def _build_external_context_text(self, contexts: list[ExternalContext]) -> str:
        if not contexts:
            return ""
        blocks = [f"[{item.label}]\n{item.body}" for item in contexts]
        return "\n\n".join(blocks)

    def _get_channel_knowledge(
        self,
        *,
        guild_id: int | None = None,
        channel_id: int | None,
        limit: int = 4,
        max_chars: int = 1200,
    ) -> str:
        if not channel_id:
            return ""
        chunks = self._local_rag.retrieve(
            "",
            limit=max(1, min(int(limit or 4), 6)),
            guild_id=guild_id,
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

    def _profile_channel_ids(
        self,
        *,
        channel: discord.abc.Messageable | None = None,
        channel_id: int | None = None,
        guild_id: int | None = None,
    ) -> list[int]:
        if guild_id:
            return [int(guild_id)]
        if channel_id:
            return [int(channel_id)]
        if channel is not None and getattr(channel, "id", None):
            return [int(getattr(channel, "id"))]
        return []

    def _get_profile_knowledge(
        self,
        *,
        channel: discord.abc.Messageable | None = None,
        channel_id: int | None = None,
        guild_id: int | None = None,
        limit: int = 4,
        max_chars: int = 1800,
    ) -> str:
        for candidate_id in self._profile_channel_ids(
            channel=channel,
            channel_id=channel_id,
            guild_id=guild_id,
        ):
            knowledge = self._get_channel_knowledge(
                guild_id=guild_id,
                channel_id=candidate_id,
                limit=limit,
                max_chars=max_chars,
            )
            if knowledge:
                return (
                    "[この場所の正式プロフィール]\n"
                    "以下はこの場所の前提です。一般テンプレート、古い assistant 発言、"
                    "推測よりも優先して扱ってください。\n"
                    "この内容と矛盾する場合は、こちらを正としてください。\n\n"
                    f"{knowledge}"
                )
        return ""

    def _build_channel_profile_block(
        self,
        *,
        channel: discord.abc.Messageable | None = None,
        channel_id: int | None = None,
        guild_id: int | None = None,
        limit: int = 4,
        max_chars: int = 1800,
    ) -> str:
        return self._get_profile_knowledge(
            channel=channel,
            channel_id=channel_id,
            guild_id=guild_id,
            limit=limit,
            max_chars=max_chars,
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
            channel=channel,
            channel_id=channel_id,
            guild_id=getattr(getattr(channel, "guild", None), "id", None),
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
                text_factory=lambda elapsed, model=model_name: self.bot.ai_progress_tracker.render(
                    ticket, elapsed, model
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

            answer = strip_ansi_and_ctrl((answer or "").strip()) or "この場所の説明を作れませんでした。"
            prefix = f"{mention}\n" if mention else ""
            await self._send_chunked_text(channel, answer, prefix=prefix)
            if source_msg is not None:
                await self._log_bot_activity_event(
                    source_msg,
                    kind="メンション",
                    processing="場所説明",
                    output_text=answer,
                    input_text=query,
                    title="Bot 会話ログ",
                    description="サーバー・チャンネル・ワールドの説明に応答しました。",
                    model_name=model_name,
                    references=self._collect_reference_labels(channel_profile_block),
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
        guild_id: int | None = None,
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
            guild_id=guild_id,
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

    def _build_file_reply_summary(self, text: str, *, max_chars: int = 120) -> str:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return "概要: なし"
        summary = normalized[:max_chars]
        if len(normalized) > max_chars:
            summary = summary.rstrip() + "..."
        return f"概要: {summary}"

    def _normalize_code_language(self, language: str) -> str:
        raw = (language or "").strip().lower()
        if not raw:
            return ""
        aliases = {
            "py": "Python",
            "python": "Python",
            "python3": "Python",
            "js": "JavaScript",
            "javascript": "JavaScript",
            "node": "JavaScript",
            "ts": "TypeScript",
            "typescript": "TypeScript",
            "sh": "Shell",
            "bash": "Shell",
            "zsh": "Shell",
            "shell": "Shell",
            "powershell": "PowerShell",
            "ps1": "PowerShell",
            "sql": "SQL",
            "json": "JSON",
            "yaml": "YAML",
            "yml": "YAML",
            "toml": "TOML",
            "html": "HTML",
            "css": "CSS",
            "md": "Markdown",
            "markdown": "Markdown",
            "c": "C",
            "cpp": "C++",
            "c++": "C++",
            "cc": "C++",
            "cxx": "C++",
            "java": "Java",
            "go": "Go",
            "golang": "Go",
            "rust": "Rust",
            "rb": "Ruby",
            "ruby": "Ruby",
            "php": "PHP",
            "perl": "Perl",
            "rs": "Rust",
        }
        return aliases.get(raw, language.strip())

    def _detect_code_language(self, text: str) -> str:
        raw = strip_ansi_and_ctrl(text or "").strip()
        if not raw:
            return ""

        fenced_blocks = re.findall(r"```([^\n`]*)\n(.*?)```", raw, re.DOTALL)
        for language, block in fenced_blocks:
            normalized = self._normalize_code_language(language)
            if normalized:
                return normalized
            candidate = block.strip()
            if not candidate:
                continue

        normalized_raw = normalize_keyword_match_text(raw)
        markers: tuple[tuple[str, str], ...] = (
            ("Python", "def "),
            ("Python", "import "),
            ("Python", "async def "),
            ("JavaScript", "console.log"),
            ("JavaScript", "function "),
            ("JavaScript", "const "),
            ("JavaScript", "let "),
            ("TypeScript", ": string"),
            ("TypeScript", "interface "),
            ("Shell", "#!/bin/bash"),
            ("Shell", "#!/usr/bin/env bash"),
            ("Shell", "curl "),
            ("Shell", "git "),
            ("SQL", "select "),
            ("SQL", "insert into "),
            ("SQL", "create table "),
            ("HTML", "<html"),
            ("HTML", "<div"),
        )
        for language, marker in markers:
            if marker in normalized_raw:
                return language

        stripped = raw.lstrip()
        if (
            stripped.startswith("{")
            or stripped.startswith("[")
        ) and ":" in raw and '"' in raw:
            return "JSON"

        if "```" in raw:
            return "コード"
        return ""

    def _infer_code_points(self, text: str) -> list[str]:
        raw = strip_ansi_and_ctrl(text or "").strip()
        if not raw:
            return []

        normalized = normalize_keyword_match_text(raw)
        points: list[str] = []
        patterns: list[tuple[str, tuple[str, ...]]] = [
            ("Discord botの処理", ("discord.", "discord.py", "commands.", "interactions.")),
            ("HTTP/API通信", ("requests.", "httpx", "aiohttp", "fetch(", "axios", "urllib", "curl ")),
            ("ファイル入出力", ("open(", "pathlib", "read_text", "write_text", "read_bytes", "write_bytes")),
            ("データベース操作", ("sqlite", "sqlalchemy", "psycopg", "cursor.execute", "select ", "insert into ")),
            ("非同期処理", ("async def", "await ", "asyncio", "create_task")),
            ("CLI引数処理", ("argparse", "click", "typer", "sys.argv")),
            ("設定/データ変換", ("json", "yaml", "toml", "dict(", "dataclass")),
            ("関数/クラスで整理", ("def ", "class ")),
        ]
        for label, needles in patterns:
            if any(needle in normalized for needle in needles):
                points.append(label)

        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) >= 4 and any(line.startswith(("if ", "for ", "while ", "try:", "except ")) for line in lines):
            points.append("分岐やループを使った処理")

        seen: set[str] = set()
        deduped: list[str] = []
        for point in points:
            if point in seen:
                continue
            seen.add(point)
            deduped.append(point)
        return deduped[:3]

    def _build_code_reply_summary(self, text: str) -> str:
        language = self._detect_code_language(text)
        points = self._infer_code_points(text)
        if language:
            head = f"{language}のコードです。"
        else:
            head = "コードです。"
        if not points:
            return f"{head} ポイント: 構造を見直して、役割ごとに分けて読むと分かりやすいです。"
        return f"{head} ポイント: {' / '.join(points)}。"

    def _looks_like_code_reply(self, text: str) -> bool:
        raw = strip_ansi_and_ctrl(text or "").strip()
        if not raw:
            return False
        if "```" in raw:
            return True
        normalized = normalize_keyword_match_text(raw)
        code_markers = (
            "def ",
            "class ",
            "import ",
            "from ",
            "if __name__ == \"__main__\"",
            "console.log",
            "function ",
            "const ",
            "let ",
            "var ",
            "#include",
            "public ",
            "private ",
            "protected ",
            "select ",
            "insert into ",
            "create table ",
            "curl ",
            "git ",
            "python ",
            "pip ",
            "npm ",
            "bash ",
        )
        if any(marker in normalized for marker in code_markers):
            return True
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) >= 4:
            code_like_lines = sum(
                1
                for line in lines
                if line.startswith(("    ", "\t", "#", ">", "$"))
                or re.match(r"^(def|class|import|from|if|for|while|return|const|let|var|function)\b", line)
                or re.match(r"^[\w./-]+(?:\s+[\w./:=-]+)+$", line)
            )
            if code_like_lines >= max(2, len(lines) // 2):
                return True
        return False

    def _extract_code_payload(self, text: str) -> str:
        raw = strip_ansi_and_ctrl(text or "").strip()
        if not raw:
            return ""
        fenced_blocks = re.findall(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", raw, re.DOTALL)
        if fenced_blocks:
            payload = "\n\n".join(block.strip("\n") for block in fenced_blocks if block.strip())
            if payload.strip():
                return payload.strip()
        return raw

    async def _send_letter_file(self, msg: discord.Message, answer: str) -> None:
        display_name = (
            getattr(msg.author, "display_name", None) or msg.author.name or "user"
        ).strip() or "user"
        prefix = (
            f"{msg.author.mention}\n"
            f"手紙を書きました。{self._build_file_reply_summary(answer)}\n"
        )
        if len(prefix) + len(answer) > 2000:
            await self._send_chunked_text(
                msg.channel,
                answer,
                prefix=prefix,
            )
            return
        await msg.channel.send(
            content=f"{prefix}{answer}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _should_send_text_file(self, answer: str, *, mention: str | None = None) -> bool:
        return self._looks_like_code_reply(answer)

    async def _send_text_file_reply(
        self,
        channel: discord.abc.Messageable,
        *,
        answer: str,
        mention: str | None = None,
        filename: str = "kennybot_reply.txt",
    ) -> None:
        prefix = f"{mention}\n" if mention else ""
        is_code_reply = self._looks_like_code_reply(answer)
        if not is_code_reply:
            await self._send_chunked_text(channel, answer, prefix=prefix)
            return
        payload_text = self._extract_code_payload(answer) if is_code_reply else answer
        payload = io.BytesIO(payload_text.encode("utf-8"))
        discord_file = discord.File(payload, filename=filename)
        summary = self._build_code_reply_summary(answer)
        message_text = f"{prefix}{summary}" if prefix else summary
        await channel.send(
            content=message_text,
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
        author_name = (
            msg.author.display_name
            if hasattr(msg.author, "display_name")
            else msg.author.name
        )
        # 総合ログに記録
        log_user_message(msg)

        text = normalize_user_text(msg.content or "")
        if not text:
            return
        text = await self._prepare_user_text_for_ai(
            text,
            max_len=self._cfg_int("security.max_user_message_chars", 1200),
            source="dm",
            msg=msg,
            channel=msg.channel,
        )
        if not text:
            return

        if self._is_runtime_model_query(text):
            await self._send_runtime_model_reply(
                msg.channel,
                mention=msg.author.mention,
                source_msg=msg,
                input_text=text,
            )
            return

        if self._is_capability_query(text):
            await self._answer_capability_query(
                msg.channel,
                text,
                mention=msg.author.mention,
                source_msg=msg,
                channel_id=msg.channel.id,
            )
            return

        if self._is_ai_channel_rate_limited(msg.channel.id):
            await msg.channel.send("少し待ってから送ってください。")
            await self._log_bot_activity_event(
                msg,
                kind="DM",
                processing="DM 会話",
                input_text=text,
                output_text="少し待ってから送ってください。",
                level="warning",
                title="Bot 会話ログ",
                description="DM の応答を間隔制限で見送りました。",
            )
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

        references: list[str] = []
        history_context, planned_refs, web_queries = await self._resolve_chat_context(
            msg=msg,
            user_display=user_name or str(msg.author.id),
            text=text,
        )
        references.extend(planned_refs)
        if self._needs_web_search_for_accuracy(text) and not self._has_web_references(
            planned_refs
        ):
            await self._handle_current_info_search_failure(
                msg.channel,
                mention=msg.author.mention,
                query=text,
                source_msg=msg,
                model_name=self._cfg_str("ollama.model_default", "gpt-oss:120b"),
                references=planned_refs,
            )
            return
        web_planned = self._has_web_references(planned_refs)
        progress_key = f"ai-progress:{msg.channel.id}:{msg.author.id}"
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        tool_queries: list[str] = []
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
        combined_history_context = history_context
        prompt = PROMPT_TEMPLATE.format(
            user_display=user_name or str(msg.author.id),
            history_context=combined_history_context,
            user_message=text,
            max_response_length_prompt=self._cfg_int(
                "chat.max_response_length_prompt", 500
            ),
        )
        chat_messages = [
            {
                "role": "system",
                "content": get_prompt("chat", "system_message").format(
                    absolute_date=datetime.now(JST).strftime("%Y-%m-%d"),
                    absolute_datetime=datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
                    channel_profile_block="",
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=msg.channel,
                mention_user_id=msg.author.id,
                text_factory=lambda elapsed, model=model_name: self.bot.ai_progress_tracker.render(
                    ticket, elapsed, model
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            await self._promote_ai_progress_message(
                progress_key=progress_key,
                ticket=ticket,
                model_name=model_name,
            )
            try:
                answer, tool_references, tool_queries = await self._run_ollama_chat_with_tools(
                    model=model_name,
                    messages=chat_messages,
                    tools=tools,
                    guild=msg.guild,
                    channel_id=msg.channel.id,
                    user_id=msg.author.id,
                )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = strip_ansi_and_ctrl((answer or "").strip())
            web_used = self._has_web_references(references + tool_references)
            if web_planned and self._should_web_followup(answer, references + tool_references):
                answer, retry_refs, retry_queries = await self._rewrite_answer_with_web(
                    model=model_name,
                    messages=chat_messages,
                    tools=tools,
                    user_request=text,
                    previous_answer=answer,
                    guild=msg.guild,
                    channel_id=msg.channel.id,
                    user_id=msg.author.id,
                )
                tool_references.extend(retry_refs)
                for ref in retry_refs:
                    if ref not in references:
                        references.append(ref)
                answer = strip_ansi_and_ctrl((answer or "").strip())
                tool_queries.extend(retry_queries)
            if not answer:
                answer = "(応答が空でした)"

            bot_name = self.bot.user.name if self.bot.user else "Bot"
            bot_id = self.bot.user.id if self.bot.user else 0
            web_urls = self._merge_unique_strings(
                self._extract_urls(answer),
                [ref for ref in references if str(ref).startswith("http")],
                [ref for ref in tool_references if str(ref).startswith("http")],
            )
            display_answer = answer
            if web_urls:
                references.extend([url for url in web_urls if url not in references])
                display_answer = self._build_display_answer_with_references(answer, web_urls)
            store.add_message(bot_name, display_answer, msg.id, author_id=bot_id)
            if self._should_send_text_file(display_answer, mention=msg.author.mention):
                await self._send_text_file_reply(
                    msg.channel,
                    answer=display_answer,
                    mention=msg.author.mention,
                )
            else:
                final_message = f"{msg.author.mention}\n{display_answer}"
                if len(final_message) > 2000:
                    await self._send_chunked_text(
                        msg.channel,
                        display_answer,
                        prefix=f"{msg.author.mention}\n",
                    )
                else:
                    await msg.channel.send(
                        final_message,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            # 総合ログにAI応答を記録
            log_ai_output(
                msg.author,
                response=answer,
                model=model_name,
                msg=msg,
                references=references,
                web_queries=web_queries + tool_queries,
            )

            await self._log_bot_activity_event(
                msg,
                kind="DM",
                processing="DM 会話",
                input_text=text,
                output_text=answer,
                model_name=model_name,
                title="Bot 会話ログ",
                description="DM の会話応答を送信しました。",
                references=references,
                web_queries=web_queries + tool_queries,
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
                references=references,
                web_queries=web_queries + tool_queries,
            )

            await self._log_bot_activity_event(
                msg,
                kind="DM",
                processing="DM 会話",
                level="error",
                title="Bot 会話ログ",
                description="DM の AI 応答処理中にエラーが発生しました。",
                input_text=text,
                error_text=str(e),
                model_name=model_name,
                references=references,
                web_queries=web_queries + tool_queries,
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

    def _collect_reference_labels(self, *texts: str) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for line in (text or "").splitlines():
                match = RAG_HEADER_RE.match(line.strip())
                if not match:
                    continue
                label = match.group(1).strip()
                if not label or label in seen:
                    continue
                seen.add(label)
                refs.append(label)
        return refs[:12]

    def _is_noisy_reference_label(self, value: str) -> bool:
        label = strip_ansi_and_ctrl((value or "").strip())
        if not label:
            return True
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", label):
            return True
        upper_label = label.upper()
        return upper_label.startswith("BOT /") or upper_label.startswith("HELP /")

    def _filter_event_references(
        self,
        references: list[str] | None,
        *,
        web_queries: list[str] | None = None,
    ) -> list[str]:
        normalized = self._merge_unique_strings(references or [])
        if not normalized:
            return []
        filtered: list[str] = []
        for ref in normalized:
            if self._is_noisy_reference_label(ref):
                continue
            filtered.append(ref)
        return filtered

    def _summarize_references(self, references: list[str] | None) -> tuple[bool, list[str], list[str]]:
        normalized = self._merge_unique_strings(references or [])
        web_used = any(
            ref.startswith("tool:web_search")
            or ref.startswith("tool:web_fetch")
            or ref.startswith("source:web_search")
            or ref.startswith("method:")
            or ref.startswith("web_search")
            or ref.startswith("web_fetch")
            for ref in normalized
        )
        return (
            web_used,
            [ref for ref in normalized if not ref.startswith("http")][:12],
            [ref for ref in normalized if ref.startswith("http")][:8],
        )

    def _build_display_answer_with_references(
        self,
        answer: str,
        web_urls: list[str],
        *,
        max_display_urls: int = 3,
    ) -> str:
        return answer

    def _format_activity_location(self, msg: discord.Message) -> str:
        if msg.guild is None:
            return f"DM ({getattr(msg.channel, 'id', 0)})"
        return f"{msg.guild.name} ({msg.guild.id}) / #{getattr(msg.channel, 'name', 'unknown')} ({getattr(msg.channel, 'id', 0)})"

    async def _log_bot_activity_event(
        self,
        msg: discord.Message,
        *,
        kind: str,
        processing: str,
        input_text: str = "",
        output_text: str = "",
        level: str = "info",
        title: str = "Bot 会話ログ",
        description: str = "Bot 関連の会話処理を記録しました。",
        error_text: str = "",
        model_name: str = "",
        references: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> None:
        normalized_references = self._filter_event_references(
            references,
            web_queries=web_queries,
        )
        fields: list[tuple[str, str, bool]] = [
            ("種別", kind, True),
            ("送信者", f"{msg.author} ({msg.author.id})", False),
            ("場所", self._format_activity_location(msg), False),
            ("メッセージID", str(msg.id), True),
            ("処理", self._truncate_event_text(processing), False),
        ]
        if input_text:
            fields.append(("入力", self._truncate_event_text(input_text), False))
        if output_text:
            fields.append(("返信", self._truncate_event_text(output_text), False))
        if model_name:
            fields.append(("モデル", self._truncate_event_text(model_name), True))
        if error_text:
            fields.append(("エラー", self._truncate_event_text(error_text), False))
        if normalized_references:
            web_used, ref_sources, ref_urls = self._summarize_references(normalized_references)
            fields.append(("Web検索", "あり" if web_used else "なし", True))
            if web_queries:
                queries = self._merge_unique_strings(web_queries)[:8]
                if queries:
                    fields.append(("検索語", "\n".join(queries), False))
            method_names = [
                ref.removeprefix("tool:").removeprefix("method:")
                for ref in normalized_references
                if str(ref).startswith("tool:") or str(ref).startswith("method:")
            ]
            if method_names:
                fields.append(
                    (
                        "参照メソッド",
                        ", ".join(self._truncate_event_text(name, 120) for name in method_names),
                        False,
                    )
                )
        if normalized_references:
            ref_lines = [
                self._truncate_event_text(ref, 400)
                for ref in normalized_references
                if str(ref).strip()
            ]
            chunk: list[str] = []
            chunk_len = 0
            part = 1
            for line in ref_lines:
                line_len = len(line) + (1 if chunk else 0)
                if chunk and chunk_len + line_len > 900:
                    fields.append((f"出典元{part}", "\n".join(chunk), False))
                    part += 1
                    chunk = [line]
                    chunk_len = len(line)
                else:
                    chunk.append(line)
                    chunk_len += line_len
            if chunk:
                label = "出典元" if part == 1 else f"出典元{part}"
                fields.append((label, "\n".join(chunk), False))
            if ref_sources:
                fields.append(
                    (
                        "参照概要",
                        ", ".join(self._truncate_event_text(ref, 120) for ref in ref_sources),
                        False,
                    )
                )
            if ref_urls:
                fields.append(
                    (
                        "参照URL",
                        "\n".join(self._truncate_event_text(url, 400) for url in ref_urls),
                        False,
                    )
                )
        await send_event_log(
            self.bot,
            guild=msg.guild,
            level=level,
            title=title,
            description=description,
            fields=fields,
            source_channel_id=getattr(msg.channel, "id", None),
        )

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
            await self._log_bot_activity_event(
                source_msg,
                kind="メンション",
                processing="モデル確認",
                input_text=input_text,
                output_text=answer,
                title="Bot 会話ログ",
                description="モデル問い合わせへ応答しました。",
                model_name=self._cfg_str("ollama.model_default", "gpt-oss:120b"),
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
        guild_id: int | None = None,
        channel_id: int | None = None,
    ) -> str:
        channel_knowledge = self._get_channel_knowledge(
            guild_id=guild_id,
            channel_id=channel_id,
            limit=4,
            max_chars=body_limit or 1200,
        )
        chunks = self._local_rag.retrieve(
            query,
            limit=limit,
            capability_only=capability_only,
            guild_id=guild_id,
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
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
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
        guild_id = int(getattr(getattr(source_msg, "guild", None), "id", 0) or 0)
        if not guild_id:
            guild_id = int(getattr(getattr(channel, "guild", None), "id", 0) or 0)
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

        normalized_query = query or ""
        channel_profile_block = self._build_channel_profile_block(
            channel=channel,
            channel_id=channel_id,
            guild_id=getattr(getattr(channel, "guild", None), "id", None),
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
                    guild_id=guild_id,
                    channel_id=channel_id,
                ),
                self._build_rag_context(
                    normalized_query,
                    limit=6,
                    capability_only=False,
                    body_limit=None,
                    guild_id=guild_id,
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
        references = self._collect_reference_labels(channel_profile_block, rag_context, updates)
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=channel,
                text_factory=lambda elapsed, model=model_name: self.bot.ai_progress_tracker.render(
                    ticket, elapsed, model
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
                await self._log_bot_activity_event(
                    source_msg,
                    kind="メンション",
                    processing="機能説明",
                    input_text=query,
                    output_text=answer,
                    title="Bot 会話ログ",
                    description="Bot の機能説明または更新情報へ応答しました。",
                    model_name=model_name,
                    references=references,
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
                await self._log_bot_activity_event(
                    source_msg,
                    kind="メンション",
                    processing="機能説明",
                    level="error",
                    title="Bot 会話ログ",
                    description="Bot の機能説明または更新情報の応答に失敗しました。",
                    input_text=query,
                    error_text=str(e),
                    model_name=model_name,
                    references=references,
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
        recent_mention_window = self._has_recent_mention_window(msg)
        should_treat_as_mention = mentioned_bot or is_reply_to_bot or recent_mention_window

        # メンション / リプライがない場合はリアクションのみ
        if not should_treat_as_mention:
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
                            send_discord=False,
                        )
                    except Exception as e:
                        logger.debug(f"Reaction failed: {e}")

            await self.bot.process_commands(msg)
            return

        if recent_mention_window:
            self._arm_recent_mention_window(msg)

        # =========================
        # ここから AI 応答処理（メンション or リプライの場合）
        # =========================
        text = normalize_user_text(content)
        if not text:
            if should_treat_as_mention:
                await msg.channel.send(
                    f"{msg.author.mention}\nはい、どうしましたか？",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._arm_recent_mention_window(msg)
                await self.bot.process_commands(msg)
                return
            await self.bot.process_commands(msg)
            return
        text = await self._prepare_user_text_for_ai(
            text,
            max_len=self._cfg_int("security.max_user_message_chars", 1200),
            source="guild",
            msg=msg,
            channel=msg.channel,
        )
        if not text:
            self._arm_recent_mention_window(msg)
            await self.bot.process_commands(msg)
            return

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
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="議事録開始",
                input_text=text,
                output_text=info,
                title="Bot 会話ログ",
                description="議事録開始を実行しました。",
            )
            self._arm_recent_mention_window(msg)
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
                await self._log_bot_activity_event(
                    msg,
                    kind="メンション",
                    processing="議事録停止",
                    input_text=text,
                    output_text="現在、進行中の議事録はありません。",
                    title="Bot 会話ログ",
                    description="進行中の議事録がなかったため停止できませんでした。",
                )
                await self.bot.process_commands(msg)
                return

            embed = self.bot.meeting_minutes.build_result_embed(msg.guild, result)  # type: ignore[attr-defined]
            await msg.channel.send(content=msg.author.mention, embed=embed)
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="議事録停止",
                input_text=text,
                output_text="議事録を停止しました。",
                title="Bot 会話ログ",
                description="議事録停止を実行しました。",
            )
            self._arm_recent_mention_window(msg)
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
            self._arm_recent_mention_window(msg)
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
            self._arm_recent_mention_window(msg)
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
            if should_treat_as_mention:
                self._arm_recent_mention_window(msg)
            await self.bot.process_commands(msg)
            return
        if self._is_ai_channel_rate_limited(msg.channel.id):
            await msg.channel.send(
                f"{msg.author.mention}\nこのチャンネルではAI応答の間隔制限中です。数秒待ってから再実行してください。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if should_treat_as_mention:
                self._arm_recent_mention_window(msg)
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

        references: list[str] = []
        today_local = datetime.now(JST)
        absolute_date = today_local.strftime("%Y-%m-%d")
        absolute_datetime = today_local.strftime("%Y-%m-%d %H:%M:%S JST")
        requires_bot_capability_grounding = self._is_bot_capability_or_game_query(text)
        mention_focus_block = ""
        history_context, planned_refs, web_queries = await self._resolve_chat_context(
            msg=msg,
            user_display=user_display,
            text=text,
        )
        references.extend(planned_refs)
        if self._needs_web_search_for_accuracy(text) and not self._has_web_references(
            planned_refs
        ):
            await self._handle_current_info_search_failure(
                msg.channel,
                mention=msg.author.mention,
                query=text,
                source_msg=msg,
                model_name=self._cfg_str("ollama.model_default", "gpt-oss:120b"),
                references=planned_refs,
            )
            return
        web_planned = self._has_web_references(planned_refs)
        progress_key = f"ai-progress:{msg.channel.id}:{msg.author.id}"
        model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
        channel_profile_block = ""
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        tool_queries: list[str] = []
        combined_history_context = history_context
        prompt = PROMPT_TEMPLATE.format(
            user_display=user_display,
            history_context=combined_history_context,
            user_message=text,
            max_response_length_prompt=self._cfg_int(
                "chat.max_response_length_prompt", 500
            ),
        )
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
        chat_messages = [
            {
                "role": "system",
                "content": get_prompt("chat", "system_message").format(
                    absolute_date=absolute_date,
                    absolute_datetime=absolute_datetime,
                    channel_profile_block=channel_profile_block,
                ),
            },
            {
                "role": "user",
                "content": (
                    (
                        person_focus_block
                        if person_focus_block
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
        ]

        try:
            await self._ai_progress_countdowns.start_countup(
                key=progress_key,
                channel=msg.channel,
                mention_user_id=msg.author.id,
                text_factory=lambda elapsed, model=model_name: self.bot.ai_progress_tracker.render(
                    ticket, elapsed, model
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            await self._promote_ai_progress_message(
                progress_key=progress_key,
                ticket=ticket,
                model_name=model_name,
            )
            try:
                answer, tool_references, tool_queries = await self._run_ollama_chat_with_tools(
                    model=model_name,
                    messages=chat_messages,
                    tools=tools,
                    guild=msg.guild,
                    channel_id=msg.channel.id,
                    user_id=msg.author.id,
                )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = strip_ansi_and_ctrl((answer or "").strip())
            if web_planned and self._should_web_followup(answer, references + tool_references):
                answer, retry_refs, retry_queries = await self._rewrite_answer_with_web(
                    model=model_name,
                    messages=chat_messages,
                    tools=tools,
                    user_request=text,
                    previous_answer=answer,
                    guild=msg.guild,
                    channel_id=msg.channel.id,
                    user_id=msg.author.id,
                )
                for ref in retry_refs:
                    if ref not in references:
                        references.append(ref)
                    if ref not in tool_references:
                        tool_references.append(ref)
                tool_queries.extend(retry_queries)
                answer = strip_ansi_and_ctrl((answer or "").strip())

            if not answer:
                answer = "(応答が空でした)"

            # Bot の応答も履歴に保存
            bot_name = self.bot.user.name if self.bot.user else "Bot"
            bot_id = self.bot.user.id if self.bot.user else 0
            web_urls = self._merge_unique_strings(
                self._extract_urls(answer),
                [ref for ref in references if str(ref).startswith("http")],
                [ref for ref in tool_references if str(ref).startswith("http")],
            )
            answer_with_refs = answer
            if web_urls:
                references.extend([url for url in web_urls if url not in references])
                answer_with_refs = self._build_display_answer_with_references(answer, web_urls)
            store.add_message(bot_name, answer_with_refs, msg.id, author_id=bot_id)

            if self._should_send_letter_file(text):
                await self._send_letter_file(msg, answer_with_refs)
            else:
                if self._should_send_text_file(answer_with_refs, mention=msg.author.mention):
                    await self._send_text_file_reply(
                        msg.channel,
                        answer=answer_with_refs,
                        mention=msg.author.mention,
                    )
                else:
                    final_message = f"{msg.author.mention}\n{answer_with_refs}"
                    if len(final_message) > 2000:
                        await self._send_chunked_text(
                            msg.channel,
                            answer_with_refs,
                            prefix=f"{msg.author.mention}\n",
                        )
                    else:
                        await msg.channel.send(
                            final_message,
                            allowed_mentions=discord.AllowedMentions.none()
                        )
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="通常会話",
                input_text=text,
                output_text=answer_with_refs,
                model_name=model_name,
                title="Bot 会話ログ",
                description="メンションまたはリプライへの AI 応答を送信しました。",
                references=references,
                web_queries=web_queries + tool_queries,
            )
            self._arm_recent_mention_window(msg)

        except Exception as e:
            logger.exception("AI response failed")
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="通常会話",
                level="error",
                title="Bot 会話ログ",
                description="メンションまたはリプライへの AI 応答に失敗しました。",
                input_text=text,
                error_text=str(e),
                model_name=model_name,
                references=references,
                web_queries=web_queries + tool_queries,
            )
            if isinstance(e, asyncio.TimeoutError):
                model_name = self._cfg_str("ollama.model_default", "gpt-oss:120b")
                await msg.channel.send(
                    f"{msg.author.mention}\nモデル準備中です。完了したらメンションで通知します。",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._arm_recent_mention_window(msg)
                asyncio.create_task(
                    self._notify_when_model_ready(
                        msg.channel,
                        channel_id=msg.channel.id,
                        user_id=msg.author.id,
                        mention=msg.author.mention,
                        model=model_name,
                    )
                )
                return
            try:
                await msg.channel.send(
                    f"{msg.author.mention}\n処理中にエラーが起きました。もう一度試してください。",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._arm_recent_mention_window(msg)
            except Exception:
                logger.exception("Failed to send AI error notice")
        finally:
            await self._ai_progress_countdowns.stop(progress_key, delete_message=True)

        # コマンド処理へ
        await self.bot.process_commands(msg)
