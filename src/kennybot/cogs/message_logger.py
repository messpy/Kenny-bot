# cogs/message_logger.py
# 会話 + リアクション

from __future__ import annotations

import json
import io
import logging
import re
import subprocess
import time
import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from src.kennybot.utils.config import PROMPT_TEMPLATE, get_app_config
from src.kennybot.utils.message_fetcher import MessageFetcher, format_messages_for_context
from src.kennybot.features.search import (
    ExternalContext,
    LiveInfoService,
    LocalRAG,
    RagChunk,
    build_channel_profile_preview,
    build_profile_chunks,
    format_profile_chunks,
    select_display_profile_chunks,
)
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.countdown import ChannelCountdown
from src.kennybot.utils.message_vector_store import MessageVectorStore
from src.kennybot.utils.command_catalog import COMMAND_CATEGORY_ORDER, HELP_SECTIONS, SLASH_COMMANDS
from src.kennybot.utils.paths import MESSAGE_VECTOR_SQLITE_PATH, ROOT_DIR, RUNTIME_STATE_DIR
from src.kennybot.utils.message_logger import (
    log_user_message,
    log_ai_output,
    log_system_event,
    log_fix_request,
    log_codex_repair_mode,
    log_codex_request,
)
from src.kennybot.utils.message_claims import MessageClaimStore
from src.kennybot.cogs.base import BaseCog
from src.kennybot.utils.channel import resolve_log_channel
from src.kennybot.utils.text import (
    normalize_user_text,
    normalize_keyword_match_text,
    strip_ansi_and_ctrl,
)
from src.kennybot.utils.prompts import get_prompt
from src.kennybot.utils.reactions import get_keyword_reactions, get_reaction_emoji
from src.kennybot.features.search import build_tool_response
from src.kennybot.utils.codex_jobs import CodexJobHandle, CodexJobManager
from src.kennybot.ai.gemini_vision import GeminiVisionError
from src.kennybot.ai.gemini_images import GeminiImageRateLimitError
from src.kennybot.ai.openai_vision import OpenAIVisionError, detect_image_mime_type
from src.kennybot.utils.vrchat_world import format_vrchat_world_text, search_vrchat_worlds
from src.kennybot.utils.tool_planner import (
    normalize_planner_plan,
    parse_json_payload as parse_planner_json_payload,
    validate_search_query,
)
from src.kennybot.utils.time import JST, now_jst
from src.kennybot.features.moderation import ModActions
from src.kennybot.features.spam import EveryoneMentionViolation, SpamGuard


logger = logging.getLogger(__name__)

if hasattr(discord, "AllowedMentions") and not hasattr(discord.AllowedMentions, "none"):
    discord.AllowedMentions.none = staticmethod(lambda: None)  # type: ignore[attr-defined]

URL_RE = re.compile(r"https?://[^\s)>\"]+")
DISCORD_MESSAGE_URL_RE = re.compile(
    r"https?://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild_id>\d{17,20})/(?P<channel_id>\d{17,20})/(?P<message_id>\d{17,20})"
)
MESSAGE_ID_REF_RE = re.compile(
    r"(?:message[_\s-]*id|メッセージ\s*ID|メッセージid)\s*[:：=]?\s*(?P<message_id>\d{17,20})",
    re.IGNORECASE,
)
RAG_HEADER_RE = re.compile(r"^\[([^\]]+)\]")
IMAGE_GENERATION_RE = re.compile(
    r"(画像|絵|イラスト|picture|image|art).{0,18}(生成|作成|作って|つくって|描いて|書いて|generate|create|draw)"
    r"|"
    r"(生成|作成|作って|つくって|描いて|書いて|generate|create|draw).{0,18}(画像|絵|イラスト|picture|image|art)",
    re.IGNORECASE,
)
IMAGE_SUBJECT_RE = re.compile(
    r"(猫|ねこ|ネコ|犬|いぬ|キャラ|キャラクター|人物|女の子|男の子|風景|背景|壁紙|アイコン|ロゴ|写真|"
    r"cat|dog|character|person|girl|boy|landscape|background|wallpaper|icon|logo|photo)",
    re.IGNORECASE,
)
IMAGE_ACTION_RE = re.compile(r"(生成|作成|作って|つくって|描いて|書いて|generate|create|draw)", re.IGNORECASE)
IMAGE_REQUEST_RE = re.compile(r"(お願い|ください|please)", re.IGNORECASE)
IMAGE_GENERATION_CLEAN_RE = re.compile(
    r"(AI)?画像(を)?(生成|作成)(して|しろ|お願い)?|画像(を)?作って|絵(を)?描いて|イラスト(を)?描いて|"
    r"generate (an? )?image|create (an? )?image|draw",
    re.IGNORECASE,
)
AI_REVIEW_EMOJI = "ai_review"

import random

_settings = get_settings()


@dataclass(frozen=True)
class AiAnswerReviewContext:
    guild_id: int | None
    channel_id: int
    question_message_id: int | None
    question_author_id: int | None
    question_text: str
    answer_text: str
    model_name: str
    references: tuple[str, ...] = ()
    reference_details: tuple[str, ...] = ()
    web_queries: tuple[str, ...] = ()


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


def _looks_like_image_generation_request(text: str) -> bool:
    normalized = normalize_user_text(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(word in lowered for word in ("説明", "解析", "分析", "describe", "analyze")):
        return False
    if IMAGE_GENERATION_RE.search(normalized):
        return True
    if "描いて" in normalized or "draw" in lowered:
        return True
    if ("画像" in normalized or "絵" in normalized or "イラスト" in normalized) and IMAGE_REQUEST_RE.search(normalized):
        return True
    return bool(IMAGE_ACTION_RE.search(normalized) and IMAGE_SUBJECT_RE.search(normalized))


def _extract_image_generation_prompt(text: str, *, bot_user_id: int | None = None) -> str:
    prompt = normalize_user_text(text)
    if bot_user_id is not None:
        prompt = re.sub(rf"<@!?{bot_user_id}>", "", prompt)
    prompt = IMAGE_GENERATION_CLEAN_RE.sub("", prompt)
    prompt = re.sub(r"^[\s:：,，。.!！?？「『]*(で|を|の|して|お願いします|ください)*", "", prompt)
    prompt = re.sub(r"[\s。.!！?？]*(お願いします|ください|して|しますね|してね)$", "", prompt)
    prompt = re.sub(r"(を)?(生成|作成|作って|つくって|描いて|書いて)$", "", prompt)
    prompt = re.sub(r"の$", "", prompt)
    return " ".join(prompt.split()).strip()


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
        self._recent_image_contexts: dict[tuple[int, int, int], tuple[float, str]] = {}
        self._topic_relation_by_message_id: dict[int, str] = {}
        # (guild_id, channel_id, user_id) -> expires_at (monotonic seconds)
        self._recent_mention_windows: dict[tuple[int, int, int], float] = {}
        self._spam_guard_disabled_guilds: set[int] = set()
        self.root = ROOT_DIR
        self._local_rag = LocalRAG(self.root)
        self._live_info = LiveInfoService()
        self._last_context_trace: dict[str, object] = {}
        self._model_ready_notifiers: set[tuple[int, int, str]] = set()
        self._vector_store = MessageVectorStore(MESSAGE_VECTOR_SQLITE_PATH)
        self._ai_retry_countdowns = ChannelCountdown()
        self._ai_progress_countdowns = ChannelCountdown()
        self._codex_job_manager = CodexJobManager(self.root)
        self._codex_job_tasks: set[asyncio.Task[None]] = set()
        self._ai_answer_reviews: dict[int, AiAnswerReviewContext] = {}
        self._ai_answer_reviews_in_progress: set[int] = set()
        self._message_claims = MessageClaimStore(
            self.root / RUNTIME_STATE_DIR / "message_claims"
        )

    def _image_attachments(self, msg: discord.Message) -> list[discord.Attachment]:
        attachments = []
        for attachment in getattr(msg, "attachments", []) or []:
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
            filename = str(getattr(attachment, "filename", "") or "").lower()
            if content_type.startswith("image/") or filename.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                attachments.append(attachment)
        return attachments[:4]

    async def _read_image_attachments(
        self,
        msg: discord.Message,
        *,
        max_total_bytes: int = 12 * 1024 * 1024,
    ) -> tuple[list[tuple[bytes, str]], list[str]]:
        images: list[tuple[bytes, str]] = []
        labels: list[str] = []
        total = 0
        for attachment in self._image_attachments(msg):
            size = int(getattr(attachment, "size", 0) or 0)
            if size and total + size > max_total_bytes:
                labels.append(f"{getattr(attachment, 'filename', 'image')}: サイズ超過で省略")
                continue
            data = await attachment.read()
            total += len(data)
            if total > max_total_bytes:
                labels.append(f"{getattr(attachment, 'filename', 'image')}: サイズ超過で省略")
                continue
            fallback_mime = str(getattr(attachment, "content_type", "") or "image/jpeg")
            images.append((data, detect_image_mime_type(data, fallback=fallback_mime)))
            labels.append(str(getattr(attachment, "filename", "") or "image"))
        return images, labels

    async def _handle_image_generation_request(self, msg: discord.Message, text: str) -> bool:
        if not bool(_settings.get("image_generation.enabled", True)):
            await msg.channel.send(
                f"{msg.author.mention}\n画像生成は現在無効です。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        provider = str(_settings.get("image_generation.provider", "gemini") or "gemini").strip().lower()
        client = (
            getattr(self.bot, "gemini_image_client", None)
            if provider == "gemini"
            else getattr(self.bot, "openai_image_client", None)
        )
        if client is None and provider == "openai":
            client = getattr(self.bot, "gemini_image_client", None)
            provider = "gemini"
        if client is None and provider == "gemini":
            client = getattr(self.bot, "openai_image_client", None)
            provider = "openai"
        if client is None:
            await msg.channel.send(
                f"{msg.author.mention}\n画像生成用の API キーが設定されていません。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        bot_user_id = self.bot.user.id if self.bot.user else None
        prompt = _extract_image_generation_prompt(text, bot_user_id=bot_user_id)
        if not prompt:
            await msg.channel.send(
                f"{msg.author.mention}\n生成したい画像の内容も一緒に書いてください。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        size = str(_settings.get("image_generation.size", "1024x1024") or "1024x1024")
        progress_key = f"image-generation:{msg.channel.id}:{msg.author.id}"
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        model_name = str(_settings.get("image_generation.model", getattr(client, "model", "gpt-image-1")))
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
            try:
                if provider == "gemini":
                    result = await asyncio.to_thread(
                        client.generate_image,
                        prompt=prompt,
                        model=model_name,
                    )
                    image_bytes = result.data
                    filename = result.filename
                else:
                    image_bytes = await asyncio.to_thread(
                        client.generate_png,
                        prompt=prompt,
                        size=size,
                        model=model_name,
                    )
                    filename = "ai-generated.png"
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            fp = io.BytesIO(image_bytes)
            fp.seek(0)
            await msg.channel.send(
                content=f"{msg.author.mention}\n生成しました。",
                file=discord.File(fp, filename=filename),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="画像生成",
                input_text=text,
                output_text=f"[image: {filename}]",
                model_name=model_name,
                title="Bot 管理ログ",
                description="メンションから AI 画像を生成して送信しました。",
            )
            return True
        except Exception as exc:
            logger.exception("Image generation failed")
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="画像生成",
                level="error",
                title="Bot 管理ログ",
                description="AI 画像生成に失敗しました。",
                input_text=text,
                error_text=str(exc),
                model_name=model_name,
            )
            if isinstance(exc, GeminiImageRateLimitError):
                text_out = "画像生成APIがレート制限またはクォータ上限に達しています。時間を置いて再試行してください。"
            else:
                text_out = "画像生成に失敗しました。プロンプトを変えてもう一度試してください。"
            await msg.channel.send(
                f"{msg.author.mention}\n{text_out}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        finally:
            await self._ai_progress_countdowns.stop(progress_key, delete_message=True)

    async def _build_image_generation_fallback_reply(self, *, prompt: str, rate_limited: bool) -> str:
        reason = (
            "画像生成APIがレート制限またはクォータ上限に達しています。"
            if rate_limited
            else "画像生成APIでエラーが発生しました。"
        )
        fallback_prompt = (
            "Discordのユーザーに返す短い日本語メッセージを作ってください。\n"
            f"状況: {reason}\n"
            f"本来生成したかった画像: {prompt}\n\n"
            "条件:\n"
            "- 画像そのものは生成できていないと明確に伝える\n"
            "- 代わりに、完成イメージの説明を2〜4文で返す\n"
            "- 最後に再利用できる画像生成プロンプトを1行で付ける\n"
            "- 余計な前置きや謝罪の連発はしない\n"
        )
        try:
            answer = await self._run_ollama_text(
                model=self._current_chat_model_name(),
                prompt=fallback_prompt,
                timeout_sec=45,
            )
        except Exception:
            logger.exception("Image generation fallback text failed")
            return ""
        return self._sanitize_user_visible_answer(answer or "")

    def _build_image_analysis_prompt(self, text: str, user_display: str) -> str:
        request = (text or "").strip()
        if not request:
            request = "この画像を説明して。"
        return (
            f"{user_display} から画像付きメッセージが届きました。\n"
            f"依頼: {request}\n\n"
            "画像から読み取れる事実を日本語で簡潔に答えてください。"
            "不確かな推測は断定せず、読めない文字や判断できない内容は不明と書いてください。"
        )

    async def _run_image_analysis(
        self,
        *,
        model: str,
        chat_messages: list[dict],
        prompt: str,
        images: list[tuple[bytes, str]],
    ) -> str | None:
        openai_vision = getattr(self.bot, "openai_vision_client", None)
        gemini_vision = getattr(self.bot, "gemini_vision_client", None)
        if openai_vision is None and gemini_vision is None:
            return "今は画像を読み取れません。少し時間を置いてもう一度送ってください。"
        system_prompt = ""
        for message in chat_messages:
            if message.get("role") == "system":
                system_prompt = str(message.get("content") or "")
                break
        if openai_vision is not None:
            try:
                return await asyncio.to_thread(
                    openai_vision.analyze_images,
                    prompt=prompt,
                    images=images,
                    system_prompt=system_prompt,
                )
            except OpenAIVisionError:
                logger.exception("OpenAI image analysis failed")
        if gemini_vision is not None:
            try:
                return await asyncio.to_thread(
                    gemini_vision.analyze_images,
                    prompt=prompt,
                    images=images,
                    system_prompt=system_prompt,
                )
            except GeminiVisionError:
                logger.exception("Gemini image analysis failed")
        return "画像解析に失敗しました。少し時間を置いてもう一度送ってください。"

    def _image_context_key(self, msg: discord.Message) -> tuple[int, int, int]:
        guild_id = int(getattr(getattr(msg, "guild", None), "id", 0) or 0)
        channel_id = int(getattr(getattr(msg, "channel", None), "id", 0) or 0)
        author_id = int(getattr(getattr(msg, "author", None), "id", 0) or 0)
        return guild_id, channel_id, author_id

    def _remember_image_context(self, msg: discord.Message, text: str) -> None:
        cleaned = strip_ansi_and_ctrl(str(text or "")).strip()
        if not cleaned:
            return
        expires_at = time.monotonic() + 30 * 60
        self._recent_image_contexts[self._image_context_key(msg)] = (
            expires_at,
            cleaned[:1200],
        )

    def _recent_image_context_block(self, msg: discord.Message) -> str:
        key = self._image_context_key(msg)
        item = self._recent_image_contexts.get(key)
        if item is None:
            return ""
        expires_at, text = item
        if expires_at <= time.monotonic():
            self._recent_image_contexts.pop(key, None)
            return ""
        return (
            "[直前の画像解析結果]\n"
            "このユーザーが直前に送った画像の解析結果です。"
            "以後の質問で「この画像」「この人」「これは」などと参照されたら、この内容を使ってください。"
            "この情報がある場合は、画像が見えない、再送してほしい、とは言わないでください。\n"
            f"{text}"
        )

    def _should_use_recent_image_context(self, text: str) -> bool:
        normalized = normalize_user_text(text or "").lower()
        if not normalized:
            return False
        markers = (
            "この画像",
            "その画像",
            "あの画像",
            "画像",
            "写真",
            "添付",
            "スクショ",
            "画面",
            "写って",
            "映って",
            "この人",
            "この方",
            "これは",
            "これって",
            "職業",
            "何して",
            "どこ",
            "誰",
        )
        return any(marker in normalized for marker in markers)

    def _claim_message_once(self, message_id: int) -> bool:
        claim_store = getattr(self, "_message_claims", None)
        if claim_store is None:
            return True
        return claim_store.claim_once(message_id)

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

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        tasks = getattr(self, "_codex_job_tasks", None)
        if tasks is None:
            tasks = set()
            self._codex_job_tasks = tasks
        tasks.add(task)

        def _finalize(done: asyncio.Task[None]) -> None:
            tasks.discard(done)
            try:
                done.result()
            except Exception:
                logger.exception("Background Codex task failed")

        task.add_done_callback(_finalize)

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

    def _stale_date_notice(self, text: str) -> str:
        current_year = now_jst().year
        years: list[int] = []
        for match in re.findall(r"20\d{2}", text or ""):
            try:
                years.append(int(match))
            except Exception:
                continue
        if any(year < current_year for year in years):
            return f"注意: 検索結果には現在（{current_year}年）より古い日付の情報が含まれます。最新条件は公式情報で確認してください。"
        return ""

    def _build_direct_web_search_answer(self, body: str) -> str:
        lines: list[str] = ["検索結果で確認できた範囲です。"]
        stale_notice = self._stale_date_notice(body)
        if stale_notice:
            lines.append(stale_notice)
        for raw_line in (body or "").splitlines():
            line = strip_ansi_and_ctrl(raw_line).strip()
            if not line:
                continue
            if line in {"検索結果", "全体要約", "回答", "補足"}:
                continue
            if line.startswith("注意:"):
                continue
            lines.append(line)
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def _has_web_search_context(self, references: list[str]) -> bool:
        return any(ref.startswith("source:web_search") for ref in references)

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
            "価格",
            "値段",
            "相場",
            "料金",
            "費用",
            "基本料金",
            "従量",
            "契約",
            "見積",
            "見積もり",
            "供給",
            "供給エリア",
            "lpガス",
            "プロパン",
            "都市ガス",
            "ガス会社",
            "ガス事業者",
            "在庫",
            "売ってる",
            "販売",
            "買える",
            "店舗",
            "店頭",
            "本当",
            "ほんとう",
            "嘘",
            "うそ",
            "正しい",
            "事実",
            "ファクトチェック",
            "出典",
            "ソース",
        )
        return any(keyword in normalized for keyword in keywords)

    @staticmethod
    def _safe_prompt_format(template: str, **kwargs: object) -> str:
        try:
            return template.format(**kwargs)
        except Exception:
            logger.exception("Prompt formatting failed")
            return template

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

    def _explicit_topic_relation(self, text: str, *, is_reply: bool = False) -> str | None:
        normalized = normalize_keyword_match_text(text or "")
        if not normalized:
            return None
        new_topic_markers = (
            "ところで",
            "話変わる",
            "話を変える",
            "別件",
            "関係ないけど",
            "それはさておき",
            "それとは別",
            "ちなみに",
        )
        if any(marker in normalized for marker in new_topic_markers):
            return "new_topic"
        continuation_markers = (
            "それ",
            "これ",
            "あれ",
            "さっき",
            "さきほど",
            "先ほど",
            "直前",
            "続き",
            "この画像",
            "その画像",
            "この人",
            "この方",
            "この件",
            "前の",
        )
        if is_reply or any(marker in normalized for marker in continuation_markers):
            return "continuation"
        return None

    async def _classify_topic_relation(
        self,
        *,
        text: str,
        recent_history: str,
        is_reply: bool = False,
    ) -> str:
        explicit = self._explicit_topic_relation(text, is_reply=is_reply)
        if explicit is not None:
            return explicit
        if not (recent_history or "").strip():
            return "new_topic"

        prompt = (
            "あなたはDiscord botの内部分類器です。\n"
            "最新メッセージが直近会話の続きか、別の新しい話題かを判定してください。\n"
            "出力はJSONのみです。説明、Markdown、コードフェンスは禁止です。\n"
            "{\"relation\":\"continuation\" または \"new_topic\", \"confidence\":0.0〜1.0}\n\n"
            "判定基準:\n"
            "- 代名詞、前の回答への質問、画像/添付への参照、返信メッセージなら continuation\n"
            "- 明示的な話題転換、独立した質問、前文脈なしで答えられる質問なら new_topic\n"
            "- 迷う場合は new_topic を選ぶ\n\n"
            f"[直近会話]\n{recent_history[:1600] or 'なし'}\n\n"
            f"[最新メッセージ]\n{text[:800]}"
        )
        model_name = self._current_chat_model_name()
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                    format="json",
                ),
                timeout=min(8, max(4, self._cfg_ai_timeout())),
            )
            payload = self._parse_json_payload(raw or "")
            if isinstance(payload, dict):
                relation = str(payload.get("relation") or "").strip().lower()
                confidence = float(payload.get("confidence") or 0)
                if relation in {"continuation", "new_topic"} and confidence >= 0.55:
                    return relation
        except Exception:
            logger.debug("Failed to classify topic relation", exc_info=True)
        return "new_topic"

    def _filter_plan_for_topic_relation(
        self,
        plan: list[dict[str, object]],
        *,
        relation: str,
        text: str,
    ) -> list[dict[str, object]]:
        if relation != "new_topic":
            return plan
        blocked_sources = {
            "recent_turns",
            "reply_chain",
            "channel_history",
            "semantic_history",
        }
        if not self._is_local_activity_query(text):
            blocked_sources.update({"recent_user_history", "member_history"})
        filtered = [
            item
            for item in plan
            if str(item.get("source") or "").strip().lower() not in blocked_sources
        ]
        return filtered

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

    def _prioritize_mentioned_person_plan(
        self,
        *,
        plan: list[dict[str, object]],
        text: str,
        target_candidates: dict[str, tuple[int, str]],
        user_lines: int,
    ) -> list[dict[str, object]]:
        if not plan or not self._is_person_lookup_query(text):
            return plan
        preferred_mention_target = next(
            (
                key
                for key in target_candidates.keys()
                if key.startswith("mentioned_")
            ),
            None,
        )
        if not preferred_mention_target:
            return plan

        adjusted: list[dict[str, object]] = []
        saw_person_source = False
        for item in plan:
            candidate = dict(item)
            source = str(candidate.get("source") or "").strip().lower()
            target = str(candidate.get("target") or "author").strip().lower()
            if source == "recent_user_history":
                candidate["source"] = "member_history"
                candidate["target"] = preferred_mention_target
                saw_person_source = True
                adjusted.append(candidate)
                continue
            if source in {"member_history", "member_profile"}:
                saw_person_source = True
                if target == "author":
                    candidate["target"] = preferred_mention_target
            adjusted.append(candidate)

        if not saw_person_source:
            adjusted.insert(
                0,
                {
                    "source": "member_profile",
                    "target": preferred_mention_target,
                },
            )
            adjusted.insert(
                1,
                {
                    "source": "member_history",
                    "target": preferred_mention_target,
                    "limit": min(max(user_lines, 6), 24),
                },
            )
        return adjusted[:8]

    def _force_channel_profile_plan(
        self,
        *,
        plan: list[dict[str, object]],
        text: str,
        channel_profile_available: bool,
    ) -> list[dict[str, object]]:
        if not self._is_channel_profile_query(text):
            return plan

        if channel_profile_available:
            return [{"source": "channel_profile"}]

        forced: list[dict[str, object]] = [{"source": "channel_profile"}]
        for item in plan:
            source = str(item.get("source") or "").strip().lower()
            if source == "channel_profile":
                continue
            if source in {
                "recent_turns",
                "reply_chain",
                "channel_history",
                "local_knowledge",
                "bot_command_catalog",
                "bot_game_catalog",
                "runtime_model",
                "vrchat_world",
                "web_search",
            }:
                continue
            forced.append(dict(item))
        return forced[:8]

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
        if self._is_fix_request_report(text):
            plan.append(
                {
                    "source": "local_knowledge",
                    "query": text,
                    "limit": 4,
                    "capability_only": True,
                }
            )
            plan.append({"source": "runtime_model"})
            plan.append({"source": "bot_command_catalog"})
            plan.append({"source": "recent_turns", "limit": min(max(channel_lines, 4), 8)})
            return plan
        if self._is_channel_profile_query(text):
            if has_profile:
                plan.append({"source": "channel_profile"})
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
        fetcher = MessageFetcher.get_instance()
        user_lines = self._cfg_int("chat.user_history_lines", 24)
        channel_lines = self._cfg_int("chat.channel_history_lines", 16)
        target_candidates = self._context_target_candidates(msg)
        recent_messages = await fetcher.fetch_recent(msg.channel, max(2, min(channel_lines, 8)))
        recent_history = format_messages_for_context(recent_messages)
        topic_relation = await self._classify_topic_relation(
            text=text,
            recent_history=recent_history,
            is_reply=bool(msg.reference),
        )
        self._topic_relation_by_message_id[int(msg.id)] = topic_relation
        channel_profile_block = self._build_channel_profile_block(
            channel=msg.channel,
            channel_id=channel_id,
            guild_id=guild_id,
            limit=4,
            max_chars=1800,
        )
        tool_menu = "\n".join(
            [
                "- serverinfo: サーバー説明、目的、参加方法、Bot の使い方など",
                "- rag: 過去ログ、ナレッジ、Bot 仕様、サーバー固有情報など",
                "- web_search: 最新情報、時事、天気、価格、在庫、API 仕様など",
            ]
        )
        prompt = self._safe_prompt_format(
            get_prompt("chat", "retrieval_plan_prompt"),
            user_display=user_display,
            guild_id=guild_id,
            guild_name=guild_name,
            channel_id=channel_id,
            channel_name=channel_name,
            latest_message=text,
            recent_history=recent_history or "なし",
            tool_menu=tool_menu,
            channel_profile_available=str(bool(channel_profile_available)).lower(),
            channel_profile_block=channel_profile_block or "なし",
        )
        model_name = self._current_chat_model_name()
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                    format="json",
                ),
                timeout=min(20, max(8, self._cfg_ai_timeout())),
            )
            plan = self._normalize_retrieval_plan(self._parse_json_payload(raw or ""))
            if plan:
                return self._filter_plan_for_topic_relation(
                    plan,
                    relation=topic_relation,
                    text=text,
                )
        except Exception:
            logger.exception("Failed to build retrieval plan via AI")
        fallback_plan = self._fallback_retrieval_plan(
            text=text,
            user_lines=user_lines,
            channel_lines=channel_lines,
            has_profile=bool(channel_profile_available),
        )
        return self._filter_plan_for_topic_relation(
            fallback_plan,
            relation=topic_relation,
            text=text,
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
                timeout=max(20, self._cfg_ai_timeout()),
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
            lines.append("検索結果")
            lines.append("注意: 以下のタイトル・日付・URL・抜粋だけを根拠にし、出典にない具体事項は確認できないと扱うこと。")
            for item in ranked_items[:2]:
                date_str = item.date or "日付未確認"
                snippet = f"\n{item.snippet.strip()}" if item.snippet.strip() else ""
                lines.append(f"- {date_str}: {item.title}\n  {item.url}{snippet}")
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
            item_lines.append("検索結果")
            item_lines.append("注意: 以下のタイトル・日付・URL・抜粋だけを根拠にし、出典にない具体事項は確認できないと扱うこと。")
            for item in ranked_items[:2]:
                date_str = item.date or "日付未確認"
                snippet = f"\n{item.snippet.strip()}" if item.snippet.strip() else ""
                item_lines.append(f"- {date_str}: {item.title}\n  {item.url}{snippet}")
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
                title="Bot 管理ログ",
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
            model_name = self._cfg_ai_model("embedding")
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
        timestamp = now_jst().isoformat()
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
            and hasattr(msg.reference.resolved, "author")
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
    ) -> tuple[str, list[str], list[str], list[str], str]:
        return await self._build_planned_context(
            msg=msg,
            user_display=user_display,
            text=text,
        )

        guild_id = msg.guild.id if msg.guild else 0
        channel_id = msg.channel.id
        user_id = msg.author.id
        guild_name = msg.guild.name if msg.guild else "DM"
        channel_name = (
            msg.channel.name if hasattr(msg.channel, "name") else str(msg.channel.id)
        )
        fetcher = MessageFetcher.get_instance()
        user_lines = self._cfg_int("chat.user_history_lines", 24)
        channel_lines = self._cfg_int("chat.channel_history_lines", 16)
        target_candidates = self._context_target_candidates(msg)
        reference_details: list[str] = []

        def _append_reference_detail(*parts: str) -> None:
            detail = " ".join(
                part.strip() for part in parts if str(part or "").strip()
            ).strip()
            if detail:
                reference_details.append(detail)

        async def get_user_history(lines: int = user_lines) -> str:
            lines = max(1, min(int(lines or user_lines), max(1, user_lines)))
            messages = await fetcher.fetch_user_recent(msg.channel, user_id, lines)
            _append_reference_detail(
                "recent_user_history",
                "target=author",
                f"lines={lines}",
                f"count={len(messages)}",
                f"message_ids=[{', '.join(str(m.id) for m in messages)}]",
            )
            return format_messages_for_context(messages)

        async def get_member_history(target: str = "author", lines: int = user_lines) -> str:
            target_key = (target or "author").strip().lower()
            target_info = (
                target_candidates.get(target_key) or target_candidates["author"]
            )
            lines = max(1, min(int(lines or user_lines), max(1, user_lines)))
            messages = await fetcher.fetch_user_recent(msg.channel, target_info[0], lines)
            _append_reference_detail(
                "member_history",
                f"target={target_key}",
                f"user_id={target_info[0]}",
                f"lines={lines}",
                f"count={len(messages)}",
                f"message_ids=[{', '.join(str(m.id) for m in messages)}]",
            )
            return format_messages_for_context(messages)

        async def get_channel_history(lines: int = channel_lines) -> str:
            lines = max(1, min(int(lines or channel_lines), max(1, channel_lines)))
            messages = await fetcher.fetch_recent(msg.channel, lines)
            _append_reference_detail(
                "channel_history",
                f"lines={lines}",
                f"count={len(messages)}",
                f"message_ids=[{', '.join(str(m.id) for m in messages)}]",
            )
            return format_messages_for_context(messages)

        async def get_recent_turns(lines: int = 6) -> str:
            lines = max(1, min(int(lines or 6), 12))
            messages = await fetcher.fetch_recent(msg.channel, lines)
            _append_reference_detail(
                "recent_turns",
                f"lines={lines}",
                f"count={len(messages)}",
                f"message_ids=[{', '.join(str(m.id) for m in messages)}]",
            )
            return format_messages_for_context(messages)

        async def get_reply_chain(lines: int = 4) -> str:
            lines = max(1, min(int(lines or 4), 8))
            messages = await fetcher.fetch_recent(msg.channel, max(lines * 2, 6))
            if not messages:
                return ""

            if (
                msg.reference
                and msg.reference.resolved
                and isinstance(msg.reference.resolved, discord.Message)
            ):
                reference_id = msg.reference.resolved.id
                chain: list[discord.Message] = []
                for item in messages:
                    if int(item.id) == int(reference_id):
                        chain.append(item)
                chain.extend(messages[-lines:])
                deduped: list[discord.Message] = []
                seen_ids: set[int] = set()
                for item in chain:
                    if item.id and item.id in seen_ids:
                        continue
                    if item.id:
                        seen_ids.add(item.id)
                    deduped.append(item)
                messages = deduped[-lines:]
            else:
                reference_id = 0
                messages = messages[-lines:]
            _append_reference_detail(
                "reply_chain",
                f"reference_id={reference_id}" if reference_id else "",
                f"lines={lines}",
                f"count={len(messages)}",
                f"message_ids=[{', '.join(str(m.id) for m in messages)}]",
            )
            return format_messages_for_context(messages)

        def get_semantic_history(
            scope: str = "channel", k: int = 6, target: str = "author"
        ) -> str:
            return f"scope={scope}, k={k}, target={target}"

        def get_local_knowledge(
            query: str = "", limit: int = 4, capability_only: bool = False
        ) -> str:
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
        plan = self._force_channel_profile_plan(
            plan=plan,
            text=text,
            channel_profile_available=bool(channel_profile_block),
        )
        plan = self._prioritize_mentioned_person_plan(
            plan=plan,
            text=text,
            target_candidates=target_candidates,
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
        reference_details: list[str] = []
        web_queries: list[str] = []
        direct_web_answer = ""
        used_sources: list[str] = []
        context_trace: dict[str, object] = {
            "mode": "planned_context",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": msg.author.id,
            "text": text,
            "blocks": [],
            "references": [],
            "web_queries": [],
            "details": [],
        }
        preferred_mention_target = next(
            (
                key
                for key in target_candidates.keys()
                if key.startswith("mentioned_")
            ),
            None,
        )
        prefer_mentioned_targets = bool(preferred_mention_target) and bool(msg.mentions)
        mention_focus_block = ""
        if prefer_mentioned_targets:
            mention_lines = [
                "[この会話で明示された人物候補]",
            ]
            for key, (member_id, display_name) in target_candidates.items():
                if not key.startswith("mentioned_"):
                    continue
                mention_lines.append(f"- {key}: {display_name} ({member_id})")
            mention_lines.append(
                "この質問に人物が関わるなら、上の mention 候補を author より優先して解釈すること。"
            )
            mention_focus_block = "\n".join(mention_lines) + "\n\n"

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

            if source == "recent_user_history":
                lines = int(limit) if isinstance(limit, int) else user_lines
                body = await get_user_history(lines)
                title = f"このユーザーの最近の発言 {lines} 件以内"
            elif source == "member_history":
                lines = int(limit) if isinstance(limit, int) else user_lines
                body = await get_member_history(target=target, lines=lines)
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
                _append_reference_detail(
                    "member_profile",
                    f"target={target}",
                    f"user_id={target_info[0]}",
                )
            elif source == "recent_turns":
                lines = int(limit) if isinstance(limit, int) else 6
                body = await get_recent_turns(lines)
                title = "このチャンネルの直近会話"
            elif source == "reply_chain":
                lines = int(limit) if isinstance(limit, int) else 4
                body = await get_reply_chain(lines)
                title = "直前の会話チェーン"
            elif source == "channel_history":
                lines = int(limit) if isinstance(limit, int) else channel_lines
                body = await get_channel_history(lines)
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
                        _append_reference_detail(
                            "semantic_history",
                            f"scope={scope_value}",
                            f"target={target}",
                            f"query={query}",
                            f"limit={limit_value}",
                            f"count={len(rows)}",
                            f"message_ids=[{', '.join(str(row.get('message_id') or '') for row in rows if row.get('message_id'))}]",
                        )
            elif source == "channel_profile":
                body = channel_profile_block
                title = "この場所の正式プロフィール"
                _append_reference_detail(
                    "channel_profile",
                    f"guild_id={guild_id}",
                    f"channel_id={channel_id}",
                )
            elif source == "local_knowledge":
                body = get_local_knowledge(
                    query=query,
                    limit=int(limit) if isinstance(limit, int) else 4,
                    capability_only=capability_only,
                )
                title = "Bot ローカル資料"
                _append_reference_detail(
                    "local_knowledge",
                    f"query={query}",
                    f"limit={int(limit) if isinstance(limit, int) else 4}",
                    f"capability_only={capability_only}",
                )
            elif source == "bot_command_catalog":
                body = self._get_bot_command_catalog(str(item.get("category") or ""))
                title = "Bot コマンド一覧"
                _append_reference_detail(
                    "bot_command_catalog",
                    f"category={str(item.get('category') or '')}",
                )
            elif source == "bot_game_catalog":
                body = self._get_bot_game_catalog()
                title = "Bot ゲーム一覧"
                _append_reference_detail("bot_game_catalog")
            elif source == "runtime_model":
                body = self._get_runtime_model_info()
                title = "現在のモデル設定"
                _append_reference_detail("runtime_model")
            elif source == "vrchat_world":
                body = self._search_vrchat_world(
                    keyword=query or text or "",
                    count=int(limit) if isinstance(limit, int) else 5,
                    author=str(item.get("author") or ""),
                    tag=str(item.get("tag") or ""),
                )
                title = "VRChat ワールド検索結果"
                _append_reference_detail(
                    "vrchat_world",
                    f"query={query or text or ''}",
                    f"count={int(limit) if isinstance(limit, int) else 5}",
                    f"author={str(item.get('author') or '')}",
                    f"tag={str(item.get('tag') or '')}",
                )
            elif source == "web_search":
                body, web_refs, web_titles, search_queries = await self._build_current_info_context(
                    query or text or "",
                    web_scope=web_scope,
                )
                references.extend(web_refs)
                title = "検索結果の要約"
                web_queries.extend([q for q in search_queries if q])
                _append_reference_detail(
                    "web_search",
                    f"query={query or text or ''}",
                    f"web_scope={web_scope}",
                    f"search_queries=[{', '.join(q for q in search_queries if q)}]",
                )
            else:
                continue

            if body:
                blocks.append((title or source, body))
                references.extend(self._collect_reference_labels(body))
                if source not in used_sources:
                    used_sources.append(source)

        topic_relation = self._topic_relation_by_message_id.pop(
            int(getattr(msg, "id", 0) or 0),
            "continuation",
        )
        if not blocks and topic_relation != "new_topic":
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
                    _append_reference_detail(
                        "semantic_history",
                        "scope=channel",
                        f"query={text}",
                        f"count={len(rows)}",
                        f"message_ids=[{', '.join(str(row.get('message_id') or '') for row in rows if row.get('message_id'))}]",
                    )

        if self._needs_web_search_for_accuracy(text) and not any(
            ref.startswith("source:web_search") for ref in references
        ):
            details.append("web_search_requested=true")

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

        for source in used_sources:
            source_ref = f"source:{source}"
            if source_ref not in references:
                references.append(source_ref)

        context_trace["blocks"] = [
            {"title": title, "body": body}
            for title, body in blocks
            if str(body or "").strip()
        ]
        context_trace["references"] = list(references)
        context_trace["web_queries"] = list(web_queries)
        context_trace["details"] = list(details)
        self._last_context_trace = context_trace
        return (
            self._build_history_context(blocks),
            self._merge_unique_strings(references),
            self._merge_unique_strings(web_queries),
            self._merge_unique_strings(reference_details),
            direct_web_answer,
        )

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
    ) -> tuple[str | None, list[str], list[str], list[str]]:
        response = await asyncio.to_thread(
            self.bot.ollama_client.chat,
            model=model,
            messages=messages,
            stream=False,
        )
        answer = ""
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
        return answer, [], [], []

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

    async def _run_ollama_text(
        self, model: str, prompt: str, *, timeout_sec: int | None = None
    ) -> str | None:
        effective_timeout = timeout_sec
        if effective_timeout is None or effective_timeout <= 0:
            effective_timeout = self._cfg_ai_timeout()
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

    def _cfg_int_list(self, path: str) -> list[int]:
        raw = _settings.get(path, [])
        values = raw if isinstance(raw, (list, tuple, set)) else []
        out: list[int] = []
        for value in values:
            try:
                out.append(int(value))
            except Exception:
                continue
        return out

    def _is_authoritative_correction_author(self, author: object) -> bool:
        try:
            author_id = int(getattr(author, "id", 0) or 0)
        except Exception:
            return False
        return author_id in set(self._cfg_int_list("admin.authoritative_correction_user_ids"))

    def _cfg_ai_model(self, target: str) -> str:
        models = get_app_config().ai_models()
        if target == "chat":
            return models.chat
        if target == "summary":
            return models.summary
        if target == "embedding":
            return models.embedding
        return models.default

    def _cfg_ai_timeout(self) -> int:
        return get_app_config().ai_models().timeout_sec

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
            "さーばー",
            "サーバ",
            "さーば",
            "チャンネル",
            "ワールド",
            "このサーバー",
            "このさーばー",
            "このサーバ",
            "このさーば",
            "このチャンネル",
            "このワールド",
            "ここ",
            "この場所",
            "何のやつ",
            "なんのやつ",
            "なにをするところ",
            "何をするところ",
            "どんなサーバー",
            "どんなチャンネル",
            "どんな場所",
            "何する",
            "何をする",
            "どんな場所",
            "用途",
            "目的",
            "概要",
            "説明",
            "何の場",
            "情報",
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

    def _is_local_activity_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
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
        normalized = normalize_keyword_match_text(text or "")
        place_subject_terms = (
            "サーバー",
            "さーばー",
            "サーバ",
            "さーば",
            "チャンネル",
            "ワールド",
            "ここ",
            "この場所",
        )
        if any(term in normalized for term in place_subject_terms) and "この人" not in normalized:
            return False
        person_subject_terms = (
            "この人",
            "この方",
            "この子",
            "このメンバー",
            "このユーザー",
            "あの人",
            "あの方",
            "<@",
        )
        person_specific_terms = (
            "どんな人",
            "どんなやつ",
            "どんな子",
            "何者",
            "誰",
            "性格",
            "特徴",
            "紹介",
        )
        activity_terms = (
            "最後の投稿",
            "最後の発言",
            "最後に投稿",
            "最後に発言",
            "最新の投稿",
            "最新の発言",
            "最近の投稿",
            "最近の発言",
            "投稿ある",
            "発言ある",
        )
        profile_terms = ("プロフィール", "ぷろふぃーる", "情報")
        if any(term in normalized for term in person_specific_terms):
            return True
        if any(term in normalized for term in activity_terms):
            return True
        if any(term in normalized for term in profile_terms):
            return any(term in normalized for term in person_subject_terms)
        return False

    def _is_mentioned_person_lookup_query(self, msg: discord.Message, text: str) -> bool:
        if not self._is_person_lookup_query(text):
            return False
        normalized = normalize_keyword_match_text(text or "")
        place_subject_terms = (
            "サーバー",
            "さーばー",
            "サーバ",
            "さーば",
            "チャンネル",
            "ワールド",
            "ここ",
            "この場所",
        )
        if any(term in normalized for term in place_subject_terms) and "この人" not in normalized:
            return False
        bot_user_id = self.bot.user.id if getattr(self.bot, "user", None) else 0
        for member in getattr(msg, "mentions", []) or []:
            member_id = int(getattr(member, "id", 0) or 0)
            if not member_id or member_id == bot_user_id:
                continue
            if bool(getattr(member, "bot", False)):
                continue
            return True
        return False

    def _is_fix_request_report(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
        issue_terms = (
            "要件",
            "直して",
            "修正",
            "直せ",
            "バグ",
            "不具合",
            "問題",
            "反映されてない",
            "反映されていない",
            "反映漏れ",
            "おかしい",
            "変",
            "違う",
            "指摘",
            "文句",
            "改善",
            "なおして",
            "まだ",
            "なってない",
            "わかってる",
            "理解してる",
            "動いてない",
            "反映",
            "届いてない",
            "混ざる",
            "txt",
            "テキスト",
            "テキストにする",
            "txtにする",
            "txt化",
            "ディスコに貼れば",
            "discordに貼れば",
            "貼れば良い",
            "貼ったほうがいい",
            "ファイル",
            "添付",
            "露出",
            "codex",
        )
        evidence_terms = (
            "source:recent_user_history",
            "recent_turns",
            "recent_user_history",
            "参照概要",
            "履歴",
            "ログ",
            "source:web_search",
            "source:member_history",
        )
        return any(term in normalized for term in issue_terms + evidence_terms)

    def _infer_fix_request_details(self, text: str) -> tuple[str, str]:
        normalized = normalize_keyword_match_text(
            re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", r"\1", text or "")
        )
        if any(term in normalized for term in ("source:recent_user_history", "recent_turns", "recent_user_history", "参照概要")):
            return (
                "会話履歴の参照表示",
                "recent_turns / source:recent_user_history をそのまま露出せず、自然文の修正予定ログに置き換える",
            )
        if any(term in normalized for term in ("unknown interaction", "help", "/help")):
            return (
                "slash help",
                "/help の応答経路とエラーログを再点検し、失敗時は詳細を管理ログに残す",
            )
        if any(term in normalized for term in ("ログ", "履歴")):
            return (
                "ログ表示",
                "ログ出力の要約を具体化し、失敗時はスタックトレースを含めて記録する",
            )
        return (
            "一般的な応答品質",
            "ユーザー指摘を確認して、該当コードの挙動を修正する",
        )

    async def _extract_previous_turn_context(self, msg: discord.Message) -> tuple[str, str]:
        if msg.guild is None:
            return "", ""
        bot_user = self.bot.user
        if bot_user is None:
            return "", ""
        fetcher = MessageFetcher.get_instance()
        messages = await fetcher.fetch_recent(msg.channel, 50)
        if not messages:
            return "", ""

        current_index = None
        for idx in range(len(messages) - 1, -1, -1):
            item = messages[idx]
            if int(item.id) == int(msg.id) and int(item.author.id) == int(msg.author.id):
                current_index = idx
                break
        if current_index is None:
            current_index = len(messages)

        prior = messages[:current_index]
        bot_id = int(bot_user.id)

        response_index = None
        for idx in range(len(prior) - 1, -1, -1):
            if int(prior[idx].author.id) == bot_id:
                response_index = idx
                break
        if response_index is None:
            return "", ""

        response_msg = prior[response_index]
        response_id = int(response_msg.id)

        prompt_msg = None
        for idx in range(response_index - 1, -1, -1):
            item = prior[idx]
            if int(item.id) != response_id:
                continue
            if int(item.author.id) == bot_id:
                continue
            prompt_msg = item
            break

        if prompt_msg is None:
            for idx in range(response_index - 1, -1, -1):
                item = prior[idx]
                if int(item.author.id) != bot_id:
                    prompt_msg = item
                    break

        prompt_text = ""
        if prompt_msg is not None:
            try:
                dt = prompt_msg.created_at.astimezone(JST)
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = ""
            prompt_text = (
                f"[{time_str}] "
                f"{prompt_msg.author}: "
                f"{prompt_msg.content}"
            ).strip()

        try:
            dt = response_msg.created_at.astimezone(JST)
            time_str = dt.strftime("%H:%M")
        except Exception:
            time_str = ""
        response_text = (
            f"[{time_str}] "
            f"{response_msg.author}: "
            f"{response_msg.content}"
        ).strip()
        return prompt_text, response_text

    async def _decide_fix_mode(
        self,
        *,
        issue: str,
        previous_prompt: str,
        previous_response: str,
    ) -> dict[str, str | bool]:
        prompt = get_prompt("chat", "repair_mode_decision_prompt").format(
            issue=issue or "不明",
            previous_prompt=previous_prompt or "取得できませんでした",
            previous_response=previous_response or "取得できませんでした",
        )
        model_name = self._current_chat_model_name()
        fallback_target, fallback_fix = self._infer_fix_request_details(issue)
        fallback = {
            "activate": True,
            "codex_mode": True,
            "reason": "ユーザーの不満が検出されたため",
            "target_area": fallback_target,
            "planned_fix": fallback_fix,
            "user_reply_hint": "指摘を受け止め、修正対象として扱う",
        }
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                    format="json",
                ),
                timeout=min(20, max(8, self._cfg_ai_timeout())),
            )
            payload = self._parse_json_payload(raw or "")
            if isinstance(payload, dict):
                return {
                    "activate": bool(payload.get("activate", True)),
                    "codex_mode": bool(payload.get("codex_mode", payload.get("activate", True))),
                    "reason": str(payload.get("reason") or fallback["reason"]),
                    "target_area": str(payload.get("target_area") or fallback_target),
                    "planned_fix": str(payload.get("planned_fix") or fallback_fix),
                    "user_reply_hint": str(
                        payload.get("user_reply_hint") or fallback["user_reply_hint"]
                    ),
                }
        except Exception:
            logger.exception("Failed to decide repair mode")
        return fallback

    async def _build_repair_user_reply(
        self,
        *,
        issue: str,
        target_area: str,
        user_reply_hint: str,
    ) -> str:
        target = (target_area or "指摘内容").strip()
        reply = f"指摘ありがとう。{target} を確認して、Discord に直接返す形で直すね。"
        return self._sanitize_user_visible_answer(reply)

    async def _start_codex_repair_job(
        self,
        *,
        issue: str,
        previous_prompt: str,
        previous_response: str,
        target_area: str,
        planned_fix: str,
    ) -> tuple[CodexJobHandle | None, str]:
        manager = getattr(self, "_codex_job_manager", None)
        if manager is None or not manager.is_available():
            return None, "codex CLI が利用できません"
        try:
            handle, monitor_task = await manager.start_job(
                issue=issue,
                previous_prompt=previous_prompt,
                previous_response=previous_response,
                target_area=target_area,
                planned_fix=planned_fix,
            )
            self._track_background_task(monitor_task)
            return handle, ""
        except Exception as exc:
            logger.exception("Failed to start Codex repair job")
            return None, strip_ansi_and_ctrl(str(exc) or "codex job start failed")

    def _should_mirror_fix_request_to_guild_rag(self, text: str, target_area: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        area = normalize_keyword_match_text(target_area or "")
        guild_terms = ("このサーバー", "サーバー", "ワールド", "このワールド", "チャンネル", "このチャンネル")
        return self._is_channel_profile_query(text) or any(term in normalized for term in guild_terms) or any(
            term in area for term in guild_terms
        )

    def _append_fix_request_to_rag(
        self,
        *,
        msg: discord.Message,
        issue: str,
        target_area: str,
        planned_fix: str,
        previous_prompt: str,
        previous_response: str,
    ) -> list[str]:
        guild = getattr(msg, "guild", None)
        channel = getattr(msg, "channel", None)
        if guild is None or channel is None:
            return []

        author = getattr(msg, "author", None)
        author_name = getattr(author, "display_name", None) or getattr(author, "name", None) or str(getattr(author, "id", "unknown"))
        question = f"ユーザー修正メモ: {issue.strip()[:80]}"
        answer_lines = [
            "ユーザーからの修正要望として保存した補足メモです。",
            f"指摘内容: {issue.strip() or '不明'}",
            f"対象: {target_area.strip() or '一般的な応答品質'}",
            f"修正方針: {planned_fix.strip() or 'ユーザー指摘に基づいて修正する'}",
        ]
        if previous_prompt.strip():
            answer_lines.append(f"直前の質問: {previous_prompt.strip()}")
        if previous_response.strip():
            answer_lines.append(f"直前の応答: {previous_response.strip()}")
        if self._is_authoritative_correction_author(author):
            answer_lines.append("このメモは管理者の訂正として扱い、後続応答で優先参照する。")
        answer = "\n".join(answer_lines)
        metadata = {
            "source": "user_fix_request",
            "author_id": getattr(author, "id", None),
            "author_name": author_name,
            "message_id": getattr(msg, "id", None),
            "channel_id": getattr(channel, "id", None),
            "guild_id": getattr(guild, "id", None),
            "target_area": target_area.strip() or "一般的な応答品質",
            "authoritative_correction": self._is_authoritative_correction_author(author),
        }
        tags = ["user_fix_request", "user_report", "repair_request"]
        if self._is_authoritative_correction_author(author):
            tags.append("authoritative_correction")

        stored_paths: list[str] = []
        try:
            path = self._local_rag.append_channel_qa(
                guild_id=int(guild.id),
                channel_id=int(channel.id),
                question=question,
                answer=answer,
                tags=tags,
                metadata=metadata,
            )
            stored_paths.append(str(path))
        except Exception:
            logger.exception("Failed to append fix request to channel RAG")

        if self._is_authoritative_correction_author(author) or self._should_mirror_fix_request_to_guild_rag(issue, target_area):
            try:
                path = self._local_rag.append_guild_qa(
                    guild_id=int(guild.id),
                    question=question,
                    answer=answer,
                    tags=tags + ["guild_scope"],
                    metadata=metadata,
                )
                stored_paths.append(str(path))
            except Exception:
                logger.exception("Failed to append fix request to guild RAG")

        return stored_paths

    async def _build_codex_repair_request(
        self,
        *,
        issue: str,
        previous_prompt: str,
        previous_response: str,
        target_area: str,
        planned_fix: str,
    ) -> str:
        prompt = get_prompt("chat", "codex_mode_prompt").format(
            issue=issue or "不明",
            previous_prompt=previous_prompt or "取得できませんでした",
            previous_response=previous_response or "取得できませんでした",
            target_area=target_area or "一般的な応答品質",
            planned_fix=planned_fix or "ユーザー指摘に基づいて修正する",
        )
        model_name = self._cfg_ai_model("summary")
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                ),
                timeout=min(20, max(8, self._cfg_ai_timeout())),
            )
            text = strip_ansi_and_ctrl((raw or "").strip())
            if text:
                return text
        except Exception:
            logger.exception("Failed to build codex repair request")
        return "\n".join(
            [
                f"- issue: {issue or '不明'}",
                f"- target_area: {target_area or '一般的な応答品質'}",
                f"- planned_fix: {planned_fix or 'ユーザー指摘に基づいて修正する'}",
                f"- previous_prompt: {previous_prompt or '取得できませんでした'}",
                f"- previous_response: {previous_response or '取得できませんでした'}",
            ]
        )

    async def _dispatch_codex_repair_logging(
        self,
        *,
        msg: discord.Message,
        issue: str,
        previous_prompt: str,
        previous_response: str,
        target_area: str,
        planned_fix: str,
        codex_job_id: str = "",
        codex_branch: str = "",
    ) -> None:
        try:
            codex_request = await self._build_codex_repair_request(
                issue=issue,
                previous_prompt=previous_prompt,
                previous_response=previous_response,
                target_area=target_area,
                planned_fix=planned_fix,
            )
            log_codex_request(
                msg=msg,
                issue=issue,
                codex_prompt=codex_request,
                target_area=target_area,
                planned_fix=planned_fix,
                previous_prompt=previous_prompt,
                previous_response=previous_response,
                job_id=codex_job_id,
                branch_name=codex_branch,
                level="warning",
            )
            log_fix_request(
                "修正予定",
                msg=msg,
                issue=issue,
                planned_fix=planned_fix,
                target_area=target_area,
                evidence="recent_user_history",
                previous_prompt=previous_prompt,
                previous_response=previous_response,
                level="warning",
            )
            if msg.guild is not None:
                await send_event_log(
                    self.bot,
                    guild=msg.guild,
                    level="warning",
                    title="codex依頼",
                    description="Codex に渡す修正依頼を記録しました。",
                    fields=[
                        ("対象", target_area, True),
                        ("問題", issue[:1000], False),
                        ("Job ID", codex_job_id or "未起票", True),
                        ("Branch", codex_branch or "未作成", False),
                        ("Codexプロンプト", codex_request[:1000], False),
                        ("前回のユーザープロンプト", previous_prompt[:1000] or "取得できませんでした", False),
                        ("前回のBot応答", previous_response[:1000] or "取得できませんでした", False),
                    ],
                    source_channel_id=getattr(msg.channel, "id", None),
                    send_discord=True,
                )
        except Exception:
            logger.exception("Failed to dispatch codex repair logging")

    async def _log_fix_request(self, msg: discord.Message, text: str) -> None:
        target_area, planned_fix = self._infer_fix_request_details(text)
        previous_prompt, previous_response = await self._extract_previous_turn_context(msg)
        repair_decision = await self._decide_fix_mode(
            issue=text,
            previous_prompt=previous_prompt,
            previous_response=previous_response,
        )
        codex_mode = True
        target_area = str(repair_decision.get("target_area") or target_area)
        planned_fix = str(repair_decision.get("planned_fix") or planned_fix)
        user_reply_hint = str(repair_decision.get("user_reply_hint") or "")
        if not bool(repair_decision.get("activate", True)):
            logger.info(
                "Repair mode classifier returned inactive, but complaint path keeps repair mode enabled"
            )
        rag_paths = self._append_fix_request_to_rag(
            msg=msg,
            issue=text,
            target_area=target_area,
            planned_fix=planned_fix,
            previous_prompt=previous_prompt,
            previous_response=previous_response,
        )
        codex_job, codex_job_error = await self._start_codex_repair_job(
            issue=text,
            previous_prompt=previous_prompt,
            previous_response=previous_response,
            target_area=target_area,
            planned_fix=planned_fix,
        )
        user_reply = await self._build_repair_user_reply(
            issue=text,
            target_area=target_area,
            user_reply_hint=user_reply_hint,
        )
        if codex_job is not None:
            user_reply = self._sanitize_user_visible_answer(
                f"{user_reply}\n修正ブランチ `{codex_job.branch_name}` を作って Codex の作業を開始しました。"
            )
        elif codex_job_error:
            user_reply = self._sanitize_user_visible_answer(
                f"{user_reply}\nただし Codex の自動修繕ジョブ起動には失敗しました。管理ログに記録しています。"
            )
        if rag_paths:
            user_reply = self._sanitize_user_visible_answer(
                f"{user_reply}\n内容はこの場所の補足メモとして保存しました。"
            )
        try:
            log_codex_repair_mode(
                msg=msg,
                trigger="user_complaint",
                issue=text,
                planned_fix=planned_fix,
                target_area=target_area,
                previous_prompt=previous_prompt,
                previous_response=previous_response,
            )
            log_fix_request(
                "修正予定",
                msg=msg,
                issue=text,
                planned_fix=planned_fix,
                target_area=target_area,
                evidence="recent_user_history",
                previous_prompt=previous_prompt,
                previous_response=previous_response,
                level="warning",
            )
        except Exception:
            logger.debug("Failed to write local fix-request log", exc_info=True)

        try:
            if user_reply:
                await msg.channel.send(
                    f"{msg.author.mention}\n{user_reply}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception:
            logger.exception("Failed to send repair acknowledgement")

        try:
            asyncio.create_task(
                self._dispatch_codex_repair_logging(
                    msg=msg,
                    issue=text,
                    previous_prompt=previous_prompt,
                    previous_response=previous_response,
                    target_area=target_area,
                    planned_fix=planned_fix,
                    codex_job_id=codex_job.job_id if codex_job is not None else "",
                    codex_branch=codex_job.branch_name if codex_job is not None else "",
                )
            )
        except Exception:
            logger.exception("Failed to schedule codex repair logging")

        try:
            await self._log_bot_activity_event(
                msg,
                kind="修正",
                processing="修正モード",
                codex_mode=True,
                input_text=text,
                output_text=user_reply,
                level="warning",
                title="Bot 管理ログ",
                description="ユーザーの指摘を修正モードとして記録しました。",
                error_text=(
                    f"target_area={target_area}; planned_fix={planned_fix}; "
                    f"codex_job_id={codex_job.job_id if codex_job is not None else 'none'}; "
                    f"branch={codex_job.branch_name if codex_job is not None else 'none'}; "
                    f"job_error={codex_job_error or 'none'}"
                ),
                references=[
                    "codex_mode",
                    "repair_mode",
                    "previous_prompt",
                    "previous_response",
                    *( [f"codex_job:{codex_job.job_id}"] if codex_job is not None else [] ),
                    *( [f"codex_branch:{codex_job.branch_name}"] if codex_job is not None else [] ),
                ],
            )
        except Exception:
            logger.exception("Failed to log repair-mode bot activity")

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

    def _extract_discord_message_refs(self, text: str) -> list[dict[str, int | None]]:
        refs: list[dict[str, int | None]] = []
        seen: set[tuple[int | None, int | None, int]] = set()
        text_without_urls = text or ""
        for match in DISCORD_MESSAGE_URL_RE.finditer(text or ""):
            guild_id = int(match.group("guild_id"))
            channel_id = int(match.group("channel_id"))
            message_id = int(match.group("message_id"))
            key = (guild_id, channel_id, message_id)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                }
            )
            text_without_urls = text_without_urls.replace(match.group(0), " ")
        for match in MESSAGE_ID_REF_RE.finditer(text_without_urls):
            message_id = int(match.group("message_id"))
            key = (None, None, message_id)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "guild_id": None,
                    "channel_id": None,
                    "message_id": message_id,
                }
            )
        return refs[:5]

    async def _resolve_channel_for_message_ref(
        self,
        *,
        msg: discord.Message,
        ref: dict[str, int | None],
    ) -> object | None:
        if msg.guild is None:
            return None
        current_guild_id = int(msg.guild.id)
        ref_guild_id = ref.get("guild_id")
        if ref_guild_id is not None and int(ref_guild_id) != current_guild_id:
            return None

        channel_id = ref.get("channel_id")
        if channel_id is None:
            try:
                row = await asyncio.to_thread(
                    self._vector_store.find_message_location,
                    guild_id=current_guild_id,
                    message_id=int(ref["message_id"] or 0),
                )
            except Exception:
                logger.debug("Failed to lookup message location from vector store", exc_info=True)
                row = None
            if row and int(row.get("guild_id") or 0) == current_guild_id:
                channel_id = int(row.get("channel_id") or 0)
        if channel_id is None:
            channel_id = int(getattr(msg.channel, "id", 0) or 0)
        if not channel_id:
            return None

        channel = None
        guild_get_channel = getattr(msg.guild, "get_channel_or_thread", None)
        if callable(guild_get_channel):
            channel = guild_get_channel(int(channel_id))
        if channel is None:
            guild_get_channel = getattr(msg.guild, "get_channel", None)
            if callable(guild_get_channel):
                channel = guild_get_channel(int(channel_id))
        if channel is None:
            bot_get_channel = getattr(self.bot, "get_channel", None)
            if callable(bot_get_channel):
                channel = bot_get_channel(int(channel_id))
        if channel is None:
            bot_fetch_channel = getattr(self.bot, "fetch_channel", None)
            if callable(bot_fetch_channel):
                try:
                    channel = await bot_fetch_channel(int(channel_id))
                except Exception:
                    channel = None

        channel_guild_id = int(getattr(getattr(channel, "guild", None), "id", 0) or 0)
        if channel_guild_id and channel_guild_id != current_guild_id:
            return None
        if not hasattr(channel, "fetch_message"):
            return None
        return channel

    def _format_referenced_discord_message(self, message: object) -> str:
        author = getattr(message, "author", None)
        author_name = (
            getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or str(getattr(author, "id", "Unknown"))
        )
        author_id = int(getattr(author, "id", 0) or 0)
        channel = getattr(message, "channel", None)
        channel_name = getattr(channel, "name", None) or str(getattr(channel, "id", "unknown"))
        channel_id = int(getattr(channel, "id", 0) or 0)
        created_at = getattr(message, "created_at", None)
        timestamp = self._format_profile_dt(created_at) if created_at else "不明"
        content = self._sanitize_for_prompt(
            str(getattr(message, "content", "") or ""),
            self._cfg_int("chat.referenced_message_max_chars", 1200),
        )
        attachments = []
        for attachment in getattr(message, "attachments", []) or []:
            filename = str(getattr(attachment, "filename", "") or "").strip()
            url = str(getattr(attachment, "url", "") or "").strip()
            label = filename or url
            if label:
                attachments.append(label)
        if not content and attachments:
            content = "(本文なし。添付あり)"
        elif not content:
            content = "(本文なし)"
        lines = [
            f"message_id: {getattr(message, 'id', '')}",
            f"channel: #{channel_name} ({channel_id})",
            f"author: {author_name} ({author_id})" if author_id else f"author: {author_name}",
            f"created_at: {timestamp}",
            f"content: {content}",
        ]
        jump_url = str(getattr(message, "jump_url", "") or "").strip()
        if jump_url:
            lines.append(f"url: {jump_url}")
        if attachments:
            lines.append(f"attachments: {', '.join(attachments[:4])}")
        return "\n".join(lines)

    async def _build_referenced_messages_context(
        self,
        *,
        msg: discord.Message,
        text: str,
    ) -> tuple[str, list[str], list[str]]:
        if msg.guild is None:
            return "", [], []
        refs = self._extract_discord_message_refs(text)
        if not refs:
            return "", [], []

        blocks: list[str] = []
        references: list[str] = []
        details: list[str] = []
        for ref in refs:
            message_id = int(ref.get("message_id") or 0)
            if message_id <= 0:
                continue
            if ref.get("guild_id") is not None and int(ref["guild_id"] or 0) != int(msg.guild.id):
                details.append(f"discord_message skipped=other_guild message_id={message_id}")
                continue
            channel = await self._resolve_channel_for_message_ref(msg=msg, ref=ref)
            if channel is None:
                details.append(f"discord_message unresolved_channel message_id={message_id}")
                continue
            try:
                message = await channel.fetch_message(message_id)
            except Exception as exc:
                exc_name = exc.__class__.__name__
                if exc_name in {"Forbidden", "NotFound"}:
                    details.append(f"discord_message inaccessible message_id={message_id}")
                else:
                    logger.debug("Failed to fetch referenced Discord message", exc_info=True)
                    details.append(f"discord_message fetch_failed message_id={message_id}")
                continue
            message_guild_id = int(getattr(getattr(message, "guild", None), "id", 0) or 0)
            if message_guild_id and message_guild_id != int(msg.guild.id):
                details.append(f"discord_message skipped=other_guild message_id={message_id}")
                continue
            blocks.append(self._format_referenced_discord_message(message))
            references.append(f"discord_message:{message_id}")
            details.append(
                f"discord_message message_id={message_id} channel_id={getattr(channel, 'id', 0)}"
            )
        if not blocks:
            return "", references, details
        return "\n\n".join(blocks), references, details

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
        root = getattr(self, "root", None) or getattr(self._local_rag, "root", None) or Path(__file__).resolve().parent.parent
        chunks = build_profile_chunks(
            root=root,
            guild_id=guild_id,
            channel_id=channel_id,
            scope="channel" if guild_id and channel_id else "guild",
            limit=max(1, min(int(limit or 4), 6)),
        )
        if not chunks:
            return ""
        display_chunks = select_display_profile_chunks(chunks)
        return format_profile_chunks(display_chunks, max_chars=max_chars)

    def _profile_channel_ids(
        self,
        *,
        channel: discord.abc.Messageable | None = None,
        channel_id: int | None = None,
        guild_id: int | None = None,
    ) -> list[int]:
        if guild_id and channel_id:
            return [int(channel_id), int(guild_id)]
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

    def _build_location_meta_block(
        self,
        *,
        msg: discord.Message,
    ) -> str:
        guild = msg.guild
        channel = msg.channel
        lines = ["[現在の場所のメタ情報]"]
        lines.append(f"サーバー名: {guild.name if guild else 'DM'}")
        if guild is not None:
            owner = getattr(guild, "owner", None)
            owner_name = getattr(owner, "display_name", None) or getattr(owner, "name", None)
            if not owner_name and getattr(guild, "owner_id", None):
                owner_name = f"ID:{guild.owner_id}"
            if owner_name:
                lines.append(f"サーバー主: {owner_name}")
            member_count = getattr(guild, "member_count", None)
            if isinstance(member_count, int) and member_count > 0:
                lines.append(f"概算メンバー数: {member_count}")
        lines.append(
            f"チャンネル名: {channel.name if hasattr(channel, 'name') else str(channel.id)}"
        )
        category = getattr(channel, "category", None)
        category_name = getattr(category, "name", "") if category is not None else ""
        if category_name:
            lines.append(f"カテゴリ: {category_name}")
        topic = getattr(channel, "topic", "")
        if topic:
            lines.append(f"チャンネル説明: {strip_ansi_and_ctrl(str(topic))}")
        return "\n".join(lines)

    def _is_member_count_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        count_terms = ("何人", "人数", "何名", "何人いる", "何人居る")
        subject_terms = ("member", "メンバー", "人", "サーバー", "このサーバー", "鯖")
        return any(term in normalized for term in count_terms) and any(
            term in normalized for term in subject_terms
        )

    def _is_server_owner_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        subject_terms = ("サーバー", "このサーバー", "鯖", "ここ")
        owner_terms = ("主", "オーナー", "管理者", "作った人", "作成者")
        return any(term in normalized for term in subject_terms) and any(
            term in normalized for term in owner_terms
        )

    def _is_top_talker_query(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(text or "")
        rank_terms = ("誰が一番", "誰が1番", "いちばん", "一番", "最も", "最多", "トップ")
        talk_terms = ("話してる", "話している", "発言", "投稿", "しゃべ", "喋")
        return any(term in normalized for term in rank_terms) and any(
            term in normalized for term in talk_terms
        )

    def _build_server_stats_snapshot(
        self,
        *,
        guild: discord.Guild,
        channel_id: int | None = None,
        scope: str = "guild",
    ) -> dict[str, object]:
        owner = getattr(guild, "owner", None)
        owner_name = getattr(owner, "display_name", None) or getattr(owner, "name", None)
        return build_tool_response(
            root=self.root,
            tool="server_stats",
            payload={
                "guild_id": guild.id,
                "channel_id": channel_id,
                "scope": scope,
                "member_count": getattr(guild, "member_count", None),
                "owner_id": getattr(guild, "owner_id", None),
                "owner_name": owner_name,
            },
        )

    async def _answer_server_stats_query(
        self,
        channel: discord.abc.Messageable,
        query: str,
        mention: str | None = None,
        source_msg: discord.Message | None = None,
    ) -> None:
        guild = getattr(source_msg, "guild", None) if source_msg is not None else None
        prefix = f"{mention}\n" if mention else ""
        if guild is None:
            await channel.send(f"{prefix}DMではサーバー情報を確認できません。")
            return

        answer = ""
        processing = "サーバー統計"
        scope = "channel" if "このチャンネル" in normalize_keyword_match_text(query or "") else "guild"
        stats = self._build_server_stats_snapshot(
            guild=guild,
            channel_id=getattr(getattr(source_msg, "channel", None), "id", None),
            scope=scope,
        )
        if self._is_server_owner_query(query):
            owner_name = str(stats.get("owner_name") or "").strip()
            owner_id = int(stats.get("owner_id") or 0)
            if owner_name:
                answer = f"このサーバーの主は {owner_name} さんです。"
            elif owner_id > 0:
                answer = f"このサーバーの主のIDは {owner_id} です。"
            else:
                answer = "このサーバーの主は確認できませんでした。"
        elif self._is_member_count_query(query):
            member_count = stats.get("member_count")
            if isinstance(member_count, int) and member_count > 0:
                answer = f"このサーバーのメンバー数は現在 {member_count} 人です。"
            else:
                answer = "このサーバーの正確なメンバー数は今の情報では確認できません。"
        elif self._is_top_talker_query(query):
            top_talkers = stats.get("top_talkers")
            if isinstance(top_talkers, list) and top_talkers:
                top = top_talkers[0]
                if isinstance(top, dict) and int(top.get("count") or 0) > 0:
                    top_name = str(top.get("author") or "不明")
                    top_count = int(top.get("count") or 0)
                    scope_label = "このチャンネル" if scope == "channel" else "保存済みログ"
                    answer = f"{scope_label}の範囲では、いま一番話しているのは {top_name} さんで {top_count} 件です。"
            if not answer:
                answer = "保存済みログの範囲では、誰が一番話しているかは特定できませんでした。"
        if not answer:
            return

        await self._send_chunked_text(channel, answer, prefix=prefix)
        if source_msg is not None:
            await self._log_bot_activity_event(
                source_msg,
                kind="メンション",
                processing=processing,
                output_text=answer,
                input_text=query,
                title="Bot 管理ログ",
                description="サーバー統計に応答しました。",
                model_name=self._current_chat_model_name(),
            )

    async def _build_planned_context(
        self,
        *,
        msg: discord.Message,
        user_display: str,
        text: str,
    ) -> tuple[str, list[str], list[str], list[str], str]:
        guild_id = msg.guild.id if msg.guild else 0
        channel_id = msg.channel.id
        guild_name = msg.guild.name if msg.guild else "DM"
        channel_name = (
            msg.channel.name if hasattr(msg.channel, "name") else str(msg.channel.id)
        )
        channel_profile_block = self._build_channel_profile_block(
            channel=msg.channel,
            channel_id=channel_id,
            guild_id=guild_id,
            limit=6,
            max_chars=2600,
        )
        location_meta_block = self._build_location_meta_block(msg=msg)
        mentioned_person_lookup = self._is_mentioned_person_lookup_query(msg, text)

        if self._is_channel_profile_query(text) and not mentioned_person_lookup:
            blocks: list[tuple[str, str]] = []
            references: list[str] = []
            details: list[str] = [
                "planner_response_mode=serverinfo_strict",
                f"serverinfo guild_id={guild_id} channel_id={channel_id}",
            ]
            if location_meta_block:
                blocks.append(("現在の場所のメタ情報", location_meta_block))
                references.append("source:location_meta")
                details.append("location_meta=on")
            if channel_profile_block:
                blocks.append(("この場所の正式プロフィール", channel_profile_block))
                references.extend(self._collect_reference_labels(channel_profile_block))
                references.append("source:serverinfo")
                details.append("serverinfo=channel_profile")
            else:
                details.append("channel_profile_missing=true")
            context_parts: list[str] = [f"[応答モード]\nserverinfo_strict"]
            context_parts.extend(
                f"[{title}]\n{body}" for title, body in blocks if str(body or "").strip()
            )
            return (
                "\n\n".join(context_parts).strip() + ("\n\n" if context_parts else ""),
                self._merge_unique_strings(references),
                [],
                self._merge_unique_strings(details),
                "",
            )

        fetcher = MessageFetcher.get_instance()
        planner_history_lines = max(2, min(self._cfg_int("chat.planner_history_lines", 4), 8))
        recent_messages = await fetcher.fetch_recent(msg.channel, planner_history_lines)
        recent_history = format_messages_for_context(recent_messages)
        tool_menu = "\n".join(
            [
                "- serverinfo: サーバー説明、目的、参加方法、Bot の使い方など",
                "- rag: 過去ログ、ナレッジ、Bot 仕様、サーバー固有情報など",
                "- web_search: 最新情報、時事、天気、価格、在庫、CVE、API 仕様など",
            ]
        )
        prompt = self._safe_prompt_format(
            get_prompt("chat", "retrieval_plan_prompt"),
            user_display=user_display,
            guild_id=guild_id,
            guild_name=guild_name,
            channel_id=channel_id,
            channel_name=channel_name,
            latest_message=text,
            recent_history=recent_history or "なし",
            tool_menu=tool_menu,
            channel_profile_available=str(bool(channel_profile_block)).lower(),
            channel_profile_block=channel_profile_block or "なし",
        )
        model_name = self._current_chat_model_name()
        plan = normalize_planner_plan(None)
        raw_plan = ""
        try:
            raw_plan = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                    format="json",
                ),
                timeout=min(20, max(8, self._cfg_ai_timeout())),
            )
            plan = normalize_planner_plan(parse_planner_json_payload(raw_plan or ""))
        except Exception:
            logger.exception("Failed to build planner context via AI")
            plan = normalize_planner_plan(None)

        if self._is_channel_profile_query(text) and not mentioned_person_lookup:
            plan["serverinfo"] = True
            rag = dict(plan.get("rag") or {})
            if not bool(rag.get("enabled")):
                rag["enabled"] = True
                rag["query"] = text[:300]
                rag["limit"] = max(1, min(self._cfg_int("chat.rag_limit", 3), 6))
            plan["rag"] = rag

        if plan["serverinfo"] and not channel_profile_block:
            plan["serverinfo"] = False
        if self._is_channel_profile_query(text) and not mentioned_person_lookup:
            plan["rag"] = {"enabled": False, "query": None, "limit": 0}
            plan["web_search"] = {"enabled": False, "query": None, "limit": 0}
        elif self._needs_web_search_for_accuracy(text):
            web_plan = plan.get("web_search") if isinstance(plan.get("web_search"), dict) else {}
            if not bool(web_plan.get("enabled")):
                plan["web_search"] = {
                    "enabled": True,
                    "query": text[:300],
                    "limit": max(1, min(self._cfg_int("chat.web_search_limit", 3), 8)),
                }

        blocks: list[tuple[str, str]] = []
        references: list[str] = []
        web_queries: list[str] = []
        direct_web_answer = ""
        details: list[str] = [
            f"planner_response_mode={plan.get('response_mode', 'normal')}",
        ]
        reason = str(plan.get("reason") or "").strip()
        if reason:
            details.append(f"planner_reason={reason}")

        referenced_messages_context, referenced_message_refs, referenced_message_details = (
            await self._build_referenced_messages_context(msg=msg, text=text)
        )
        if referenced_messages_context:
            blocks.append(("明示参照されたDiscordメッセージ", referenced_messages_context))
            references.extend(referenced_message_refs)
        details.extend(referenced_message_details)

        if recent_history:
            blocks.append(("この会話の短い履歴", recent_history))
            references.extend(self._collect_reference_labels(recent_history))

        if bool(plan.get("serverinfo")) and channel_profile_block:
            blocks.append(("この場所の正式プロフィール", channel_profile_block))
            references.extend(self._collect_reference_labels(channel_profile_block))
            references.append("source:serverinfo")
            details.append(
                f"serverinfo guild_id={guild_id} channel_id={channel_id}"
            )

        rag_plan = plan.get("rag") if isinstance(plan.get("rag"), dict) else {}
        if isinstance(rag_plan, dict) and bool(rag_plan.get("enabled")):
            rag_query = str(rag_plan.get("query") or text).strip()
            if rag_query:
                rag_limit = max(1, min(int(rag_plan.get("limit") or 3), 6))
                rag_body = self._get_local_knowledge(
                    rag_query,
                    limit=rag_limit,
                    capability_only=False,
                    max_chars=2200,
                    guild_id=guild_id,
                    channel_id=channel_id,
                )
                if rag_body:
                    blocks.append(("関連RAG", rag_body))
                    references.extend(self._collect_reference_labels(rag_body))
                    references.append("source:rag")
                    details.append(
                        f"rag query={rag_query} limit={rag_limit}"
                    )

        web_plan = plan.get("web_search") if isinstance(plan.get("web_search"), dict) else {}
        if isinstance(web_plan, dict) and bool(web_plan.get("enabled")):
            web_query = str(web_plan.get("query") or text).strip()
            ok, reason, normalized_query = validate_search_query(
                web_query,
                latest_message=text,
            )
            if ok and normalized_query:
                details.append(f"web_search query={normalized_query} limit={int(web_plan.get('limit') or 3)}")
                body, web_refs, _title_map, search_queries = await self._build_current_info_context(
                    normalized_query,
                    web_scope="auto",
                )
                if body:
                    blocks.append(("外部検索結果", body))
                    references.extend(web_refs)
                    references.extend(self._collect_reference_labels(body))
                    web_queries.extend([q for q in search_queries if q])
                    references.append("source:web_search")
                else:
                    details.append("web_search_result=empty")
            else:
                details.append(f"web_search_blocked={reason}")

        if not blocks:
            fallback = self._build_channel_profile_block(
                channel=msg.channel,
                channel_id=channel_id,
                guild_id=guild_id,
                limit=4,
                max_chars=1800,
            )
            if fallback:
                blocks.append(("この場所の正式プロフィール", fallback))
                references.extend(self._collect_reference_labels(fallback))
                references.append("source:serverinfo")
                details.append("fallback=channel_profile")

        context_parts: list[str] = [f"[応答モード]\n{plan.get('response_mode', 'normal')}"]
        context_parts.extend(f"[{title}]\n{body}" for title, body in blocks if str(body or "").strip())
        return (
            "\n\n".join(context_parts).strip() + ("\n\n" if context_parts else ""),
            self._merge_unique_strings(references),
            self._merge_unique_strings(web_queries),
            self._merge_unique_strings(details),
            direct_web_answer,
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

        location_meta_block = self._build_location_meta_block(
            msg=source_msg
            if source_msg is not None
            else type(
                "_PreviewMsg",
                (),
                {
                    "guild": getattr(channel, "guild", None),
                    "channel": channel,
                },
            )(),
        )
        channel_profile_block = self._build_channel_profile_block(
            channel=channel,
            channel_id=channel_id,
            guild_id=getattr(getattr(channel, "guild", None), "id", None),
            limit=6,
            max_chars=2600,
        )

        progress_key = f"ai-progress:{channel_id}:profile:{mention or 'anon'}"
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        preview = build_channel_profile_preview(
            root=self.root,
            guild_id=getattr(getattr(channel, "guild", None), "id", None),
            channel_id=channel_id,
            scope="auto",
            question=query,
            limit=6,
            max_chars=2600,
        )
        fallback_answer = self._sanitize_user_visible_answer(
            str(
                preview.get("answer")
                or preview.get("profile_summary")
                or ""
            ).strip()
        )
        self._last_context_trace = {
            "mode": "channel_profile",
            "guild_id": getattr(getattr(channel, "guild", None), "id", None),
            "channel_id": channel_id,
            "query": query,
            "profile": preview.get("profile"),
            "profile_summary": preview.get("profile_summary"),
            "answer": fallback_answer,
            "references": self._collect_reference_labels(channel_profile_block),
            "location_meta": location_meta_block,
        }
        prompt = self._safe_prompt_format(
            get_prompt("chat", "channel_profile_prompt"),
            query=query,
            channel_profile_block="\n\n".join(
                part for part in [location_meta_block, channel_profile_block] if str(part or "").strip()
            ),
        )
        model_name = self._current_chat_model_name()

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

            answer = strip_ansi_and_ctrl((answer or "").strip()) or fallback_answer
            if not answer:
                answer = self._sanitize_user_visible_answer(
                    "\n\n".join(
                        part for part in [location_meta_block, channel_profile_block] if str(part or "").strip()
                    ).strip()
                )
            answer = self._sanitize_user_visible_answer(answer)
            prefix = f"{mention}\n" if mention else ""
            references = self._collect_reference_labels(channel_profile_block)
            await self._send_ai_text_response(
                channel,
                answer,
                prefix=prefix,
                source_msg=source_msg,
                question_text=query,
                model_name=model_name,
                references=references,
            )
            if source_msg is not None:
                await self._log_bot_activity_event(
                    source_msg,
                    kind="メンション",
                    processing="場所説明",
                    output_text=answer,
                    input_text=query,
                    title="Bot 管理ログ",
                    description="サーバー・チャンネル・ワールドの説明に応答しました。",
                    model_name=model_name,
                    references=references,
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
            answer = fallback_answer or self._sanitize_user_visible_answer(
                "\n\n".join(
                    part for part in [location_meta_block, channel_profile_block] if str(part or "").strip()
                ).strip()
            )
            await self._send_ai_text_response(
                channel,
                answer,
                prefix=prefix,
                source_msg=source_msg,
                question_text=query,
                model_name=model_name,
                references=self._collect_reference_labels(channel_profile_block),
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
        return False

    async def _send_letter_file(self, msg: discord.Message, answer: str) -> None:
        prefix = f"{msg.author.mention}\n"
        body = self._sanitize_user_visible_answer(answer)
        message = f"{prefix}{body}" if body else prefix.rstrip("\n")
        if len(message) > 2000:
            await self._send_chunked_text(msg.channel, body, prefix=prefix)
            return
        await msg.channel.send(
            content=message,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _should_send_text_file(self, answer: str, *, mention: str | None = None) -> bool:
        return False

    async def _send_text_file_reply(
        self,
        channel: discord.abc.Messageable,
        *,
        answer: str,
        mention: str | None = None,
        filename: str = "kennybot_reply.txt",
    ) -> None:
        prefix = f"{mention}\n" if mention else ""
        raw = strip_ansi_and_ctrl(answer or "").strip()
        if not raw:
            raw = "不明です。"
        await self._send_chunked_text(channel, raw, prefix=prefix)

    def _is_ai_channel_rate_limited(self, channel_id: int) -> bool:
        now = time.time()
        cooldown = float(self._cfg_int("security.ai_channel_cooldown_seconds", 4))
        last = self._ai_channel_last.get(channel_id, 0.0)
        if now - last < cooldown:
            return True
        self._ai_channel_last[channel_id] = now
        return False

    async def _reject_if_ai_rate_limited(
        self,
        msg: discord.Message,
        *,
        spam_guard_disabled: bool,
        should_treat_as_mention: bool = False,
    ) -> bool:
        guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        if not spam_guard_disabled and not guard.allow_ai(msg.author.id):
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
            return True
        if self._is_ai_channel_rate_limited(msg.channel.id):
            await msg.channel.send(
                f"{msg.author.mention}\nこのチャンネルではAI応答の間隔制限中です。数秒待ってから再実行してください。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if should_treat_as_mention:
                self._arm_recent_mention_window(msg)
            return True
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
        text = self._sanitize_for_prompt(
            text,
            self._cfg_int("security.max_user_message_chars", 1200),
        )

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
                title="Bot 管理ログ",
                description="DM の応答を間隔制限で見送りました。",
            )
            return

        user_name = (
            msg.author.display_name
            if hasattr(msg.author, "display_name")
            else msg.author.name
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
        reference_details: list[str] = []
        history_context, planned_refs, web_queries, planned_details, direct_web_answer = await self._resolve_chat_context(
            msg=msg,
            user_display=user_name or str(msg.author.id),
            text=text,
        )
        references.extend(planned_refs)
        reference_details.extend(planned_details)
        if direct_web_answer:
            await self._send_ai_text_response(
                msg.channel,
                direct_web_answer,
                prefix=f"{msg.author.mention}\n",
                source_msg=msg,
                question_text=text,
                model_name="web_search",
                references=references,
                reference_details=reference_details,
                web_queries=web_queries,
            )
            log_ai_output(
                msg.author,
                response=direct_web_answer,
                model="web_search",
                msg=msg,
                references=references,
                reference_details=reference_details,
                web_queries=web_queries,
            )
            await self._log_bot_activity_event(
                msg,
                kind="DM",
                processing="Web検索",
                input_text=text,
                output_text=direct_web_answer,
                model_name="web_search",
                title="Bot 管理ログ",
                description="DM の Web 検索応答を送信しました。",
                references=references,
                reference_details=reference_details,
                web_queries=web_queries,
            )
            return
        if self._needs_web_search_for_accuracy(text) and not self._has_web_search_context(references):
            await self._handle_current_info_search_failure(
                msg.channel,
                mention=msg.author.mention,
                query=text,
                source_msg=msg,
                model_name="web_search",
                references=references,
            )
            return
        progress_key = f"ai-progress:{msg.channel.id}:{msg.author.id}"
        model_name = self._current_chat_model_name()
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        recent_image_context = (
            self._recent_image_context_block(msg)
            if self._should_use_recent_image_context(text)
            else ""
        )
        combined_history_context = "\n\n".join(
            part for part in (history_context, recent_image_context) if part.strip()
        )
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
                    absolute_date=now_jst().strftime("%Y-%m-%d"),
                    absolute_datetime=now_jst().strftime("%Y-%m-%d %H:%M:%S JST"),
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
                answer, tool_references, tool_queries, tool_reference_details = await self._run_ollama_chat_with_tools(
                    model=model_name,
                    messages=chat_messages,
                    tools=[],
                    guild=msg.guild,
                    channel_id=msg.channel.id,
                    user_id=msg.author.id,
                )
                reference_details.extend(tool_reference_details)
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = strip_ansi_and_ctrl((answer or "").strip())
            if not answer:
                answer = "(応答为空でした)"
            answer = self._sanitize_user_visible_answer(answer)

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
            await self._send_ai_text_response(
                msg.channel,
                display_answer,
                prefix=f"{msg.author.mention}\n",
                source_msg=msg,
                question_text=text,
                model_name=model_name,
                references=references,
                reference_details=reference_details,
                web_queries=web_queries + tool_queries,
            )

            # 総合ログにAI応答を記録
            log_ai_output(
                msg.author,
                response=answer,
                model=model_name,
                msg=msg,
                references=references,
                reference_details=reference_details,
                web_queries=web_queries + tool_queries,
            )

            await self._log_bot_activity_event(
                msg,
                kind="DM",
                processing="DM 会話",
                input_text=text,
                output_text=answer,
                model_name=model_name,
                title="Bot 管理ログ",
                description="DM の会話応答を送信しました。",
                references=references,
                reference_details=reference_details,
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
                reference_details=reference_details,
                web_queries=web_queries + tool_queries,
            )

            await self._log_bot_activity_event(
                msg,
                kind="DM",
                processing="DM 会話",
                level="error",
                title="Bot 管理ログ",
                description="DM の AI 応答処理中にエラーが発生しました。",
                input_text=text,
                error_text=str(e),
                model_name=model_name,
                references=references,
                reference_details=reference_details,
                web_queries=web_queries + tool_queries,
            )
            if isinstance(e, asyncio.TimeoutError):
                model_name = self._current_chat_model_name()
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

    @staticmethod
    def _is_everyone_mention(msg: discord.Message, content: str) -> bool:
        lowered = content.lower()
        return bool(
            getattr(msg, "mention_everyone", False)
            and ("@everyone" in lowered or "@here" in lowered)
        )

    async def _delete_everyone_violation_messages(
        self,
        msg: discord.Message,
        violation: EveryoneMentionViolation,
    ) -> int:
        deleted = 0
        seen: set[tuple[int, int]] = set()
        channel_cache: dict[int, object] = {}
        for event in violation.events:
            key = (event.channel_id, event.message_id)
            if key in seen:
                continue
            seen.add(key)

            if event.message_id == msg.id:
                channel_cache[event.channel_id] = msg.channel
                if await ModActions.delete_message(msg, "クロスチャンネル @everyone"):
                    deleted += 1
                continue

            channel = channel_cache.get(event.channel_id)
            if channel is None:
                channel = self.bot.get_channel(event.channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(event.channel_id)
                except Exception:
                    logger.exception(
                        "Failed to fetch channel for @everyone cleanup: channel_id=%s",
                        event.channel_id,
                    )
                    continue
            channel_cache[event.channel_id] = channel

            get_partial_message = getattr(channel, "get_partial_message", None)
            if get_partial_message is None:
                continue
            try:
                await get_partial_message(event.message_id).delete()
                deleted += 1
            except discord.NotFound:
                pass
            except Exception:
                logger.exception(
                    "Failed to delete @everyone violation message: channel_id=%s message_id=%s",
                    event.channel_id,
                    event.message_id,
                )

        after = discord.utils.utcnow() - timedelta(seconds=60)
        for channel_id, channel in channel_cache.items():
            history = getattr(channel, "history", None)
            if history is None:
                continue
            try:
                async for recent in history(limit=100, after=after):
                    recent_author_id = getattr(
                        getattr(recent, "author", None),
                        "id",
                        None,
                    )
                    if recent_author_id != violation.user_id:
                        continue
                    key = (channel_id, recent.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    if await ModActions.delete_message(
                        recent,
                        "クロスチャンネル @everyone の直近投稿",
                    ):
                        deleted += 1
            except discord.Forbidden:
                logger.warning(
                    "Missing permissions to clean @everyone spam history: channel_id=%s",
                    channel_id,
                )
            except Exception:
                logger.exception(
                    "Failed to clean @everyone spam history: channel_id=%s",
                    channel_id,
                )
        return deleted

    async def _handle_everyone_mention_violation(
        self,
        msg: discord.Message,
        violation: EveryoneMentionViolation,
    ) -> None:
        deleted_count = await self._delete_everyone_violation_messages(msg, violation)

        member = msg.author if isinstance(msg.author, discord.Member) else None
        if member is None:
            try:
                member = await msg.guild.fetch_member(msg.author.id)
            except Exception:
                logger.exception(
                    "Failed to fetch member for @everyone kick: guild_id=%s user_id=%s",
                    msg.guild.id,
                    msg.author.id,
                )
        action_result = None
        if member:
            action_result = await ModActions.execute_level(
                self.bot,
                msg.guild,
                member,
                "kick",
            )

        channels = ", ".join(f"<#{event.channel_id}>" for event in violation.events)
        result_text = "未実行"
        if action_result is not None:
            result_text = (
                "成功"
                if action_result.success
                else f"失敗: {action_result.detail[:160]}"
            )

        await send_event_log(
            self.bot,
            guild=msg.guild,
            level="error",
            title="🚨 @everyone/@here スパム検出",
            description=f"{msg.author.mention} が2秒以内に @everyone / @here を複数回投稿しました。",
            fields=[
                ("ユーザー", f"{msg.author} ({msg.author.id})", False),
                ("対象チャンネル", channels[:1000] or "-", False),
                ("削除", f"{deleted_count} 件", True),
                ("処罰", f"KICK: {result_text}", True),
            ],
        )

    def _read_readme_excerpt(self, max_chars: int = 6000) -> str:
        try:
            root = getattr(self, "root", None) or ROOT_DIR
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
        default_model = self._current_chat_model_name()
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

    @staticmethod
    def _collect_message_ids(messages: list[dict]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for message in messages:
            try:
                message_id = int(message.get("id", 0) or 0)
            except Exception:
                message_id = 0
            if message_id <= 0:
                continue
            message_id_text = str(message_id)
            if message_id_text in seen:
                continue
            seen.add(message_id_text)
            ids.append(message_id_text)
        return ids

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

    def _current_chat_model_name(self) -> str:
        return self._cfg_ai_model("chat")

    def _normalize_user_visible_slash_commands(self, text: str) -> str:
        allowed = set(SLASH_COMMANDS)

        def repl(match: re.Match[str]) -> str:
            command = match.group(1)
            return f"/{command}" if command in allowed else command

        return re.sub(r"(?<!\S)/([A-Za-z][A-Za-z0-9_+\-]*)\b", repl, text)

    def _sanitize_user_visible_answer(self, answer: str) -> str:
        text = strip_ansi_and_ctrl(answer or "").strip()
        if not text:
            return ""
        text = re.sub(r"（モック応答）", "", text)
        text = re.sub(r"モック応答[:：\s]*", "", text)
        replacements = (
            ("source:recent_user_history", "最近の会話履歴"),
            ("source:member_history", "この人の最近の発言"),
            ("source:recent_turns", "このチャンネルの直近会話"),
            ("recent_user_history", "最近の会話履歴"),
            ("recent_turns", "このチャンネルの直近会話"),
            ("参照概要", "参照元の概要"),
            ("参照詳細", "参照元の詳細"),
        )
        for src, dst in replacements:
            text = text.replace(src, dst)
        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                cleaned_lines.append("")
                continue
            line = re.sub(r"^\[RAG:[^\]]+\]\s*", "", line)
            line = re.sub(r"\[RAG:[^\]]+\]", "", line)
            line = re.sub(r"^.*?を優先して返しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を優先しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を案内しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を要約しました[。．.]*\s*", "", line)
            line = re.sub(r"^.*?を見て判断しました[。．.]*\s*", "", line)
            cleaned_lines.append(line)
        text = "\n".join(cleaned_lines)
        text = self._normalize_user_visible_slash_commands(text).strip()
        return text or "不明です。"

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
        codex_mode: bool = False,
        input_text: str = "",
        output_text: str = "",
        level: str = "info",
        title: str = "Bot 管理ログ",
        description: str = "Bot 関連の会話処理を記録しました。",
        error_text: str = "",
        model_name: str = "",
        references: list[str] | None = None,
        reference_details: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> None:
        if codex_mode and title == "Bot 管理ログ":
            title = "Bot 管理ログ / 修正モード"
        normalized_references = self._filter_event_references(
            references,
            web_queries=web_queries,
        )
        normalized_reference_details = self._filter_event_references(
            reference_details,
            web_queries=web_queries,
        )
        detailed_references = self._merge_unique_strings(
            normalized_references,
            normalized_reference_details,
        )
        fields: list[tuple[str, str, bool]] = [
            ("種別", kind, True),
            ("カテゴリ", self._truncate_event_text(processing or kind), True),
            ("送信者", f"{msg.author} ({msg.author.id})", False),
            ("場所", self._format_activity_location(msg), False),
            ("メッセージID", str(msg.id), True),
            ("処理", self._truncate_event_text(processing), False),
            ("Codexモード", "あり" if codex_mode else "なし", True),
            ("モード", "修正モード" if codex_mode else "通常会話", True),
        ]
        if input_text:
            fields.append(("入力", self._truncate_event_text(input_text), False))
        if output_text:
            fields.append(("返信", self._truncate_event_text(output_text), False))
        if model_name:
            fields.append(("モデル", self._truncate_event_text(model_name), True))
        if error_text:
            fields.append(("エラー", self._truncate_event_text(error_text), False))
        ref_sources: list[str] = []
        ref_urls: list[str] = []
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
        if detailed_references:
            ref_lines = [self._truncate_event_text(ref, 400) for ref in detailed_references if str(ref).strip()]
            chunk: list[str] = []
            chunk_len = 0
            part = 1
            for line in ref_lines:
                line_len = len(line) + (1 if chunk else 0)
                if chunk and chunk_len + line_len > 900:
                    fields.append((f"参照詳細{part}", "\n".join(chunk), False))
                    part += 1
                    chunk = [line]
                    chunk_len = len(line)
                else:
                    chunk.append(line)
                    chunk_len += line_len
            if chunk:
                label = "参照詳細" if part == 1 else f"参照詳細{part}"
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
                title="Bot 管理ログ",
                description="モデル問い合わせへ応答しました。",
                model_name=self._current_chat_model_name(),
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

    async def _add_ai_review_reaction(self, message: discord.Message) -> None:
        try:
            await message.add_reaction(get_reaction_emoji(AI_REVIEW_EMOJI))
        except Exception:
            logger.debug("Failed to add AI review reaction", exc_info=True)

    def _remember_ai_answer_review(
        self,
        message: discord.Message,
        *,
        source_msg: discord.Message | None,
        question_text: str,
        answer_text: str,
        model_name: str,
        references: list[str] | None = None,
        reference_details: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> None:
        channel_id = int(getattr(getattr(message, "channel", None), "id", 0) or 0)
        if channel_id <= 0:
            return
        guild = getattr(message, "guild", None) or getattr(getattr(message, "channel", None), "guild", None)
        guild_id = int(guild.id) if getattr(guild, "id", None) is not None else None
        if not hasattr(self, "_ai_answer_reviews"):
            self._ai_answer_reviews = {}
        self._ai_answer_reviews[message.id] = AiAnswerReviewContext(
            guild_id=guild_id,
            channel_id=channel_id,
            question_message_id=int(source_msg.id)
            if source_msg is not None and getattr(source_msg, "id", None) is not None
            else None,
            question_author_id=int(source_msg.author.id)
            if source_msg is not None and getattr(getattr(source_msg, "author", None), "id", None) is not None
            else None,
            question_text=question_text,
            answer_text=answer_text,
            model_name=model_name,
            references=tuple(references or ()),
            reference_details=tuple(reference_details or ()),
            web_queries=tuple(web_queries or ()),
        )
        if len(self._ai_answer_reviews) > 300:
            for old_id in list(self._ai_answer_reviews.keys())[:50]:
                self._ai_answer_reviews.pop(old_id, None)

    async def _send_ai_text_response(
        self,
        channel: discord.abc.Messageable,
        answer: str,
        *,
        prefix: str = "",
        source_msg: discord.Message | None = None,
        question_text: str = "",
        model_name: str = "",
        references: list[str] | None = None,
        reference_details: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> list[discord.Message]:
        sent_messages = await self._send_chunked_text(channel, answer, prefix=prefix)
        if not sent_messages:
            return []
        first_message = sent_messages[0]
        self._remember_ai_answer_review(
            first_message,
            source_msg=source_msg,
            question_text=question_text,
            answer_text=answer,
            model_name=model_name,
            references=references,
            reference_details=reference_details,
            web_queries=web_queries,
        )
        await self._add_ai_review_reaction(first_message)
        return sent_messages

    async def _send_chunked_text(
        self,
        channel: discord.abc.Messageable,
        text: str,
        *,
        prefix: str = "",
        chunk_size: int = 1900,
    ) -> list[discord.Message]:
        remaining = (text or "").strip()
        if not remaining:
            return []
        first = True
        sent_messages: list[discord.Message] = []
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
            sent_messages.append(
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            )
            first = False
        return sent_messages

    def _build_ai_review_prompt(self, context: AiAnswerReviewContext) -> str:
        refs = "\n".join(f"- {ref}" for ref in context.references[:12]) or "なし"
        details = "\n".join(f"- {detail}" for detail in context.reference_details[:12]) or "なし"
        queries = "\n".join(f"- {query}" for query in context.web_queries[:8]) or "なし"
        return (
            "あなたはDiscord botの回答品質レビュー担当です。\n"
            "直前の質問とAI回答を見直し、必要がある場合だけ修正版を作ってください。\n"
            "特に、検索結果依存で質問自体に答えていない、既知の事実や年代関係を組み合わせて答えられるのに止まっている、"
            "断言できること/推測/不明点の切り分けが悪い、という問題を重視してください。\n"
            "出力はJSONのみです。Markdown、コードフェンス、説明は禁止です。\n"
            "{\"needs_resend\":true/false,\"reason\":\"短い理由\",\"revised_answer\":\"必要時のみ修正版。不要なら空文字\"}\n\n"
            "判断基準:\n"
            "- 元回答が十分なら needs_resend=false\n"
            "- 修正版はユーザーへそのまま再送できる自然な文にする\n"
            "- 不明点は限定して述べ、確認できることまで巻き込んで不明扱いしない\n"
            "- 参照情報がある場合はそれを無視しない。ただし参照情報に直接ない事実でも、一般知識や年代関係で堅く言えることは整理する\n\n"
            f"[質問]\n{context.question_text[:1800]}\n\n"
            f"[元回答]\n{context.answer_text[:2600]}\n\n"
            f"[参照]\n{refs}\n\n"
            f"[参照詳細]\n{details}\n\n"
            f"[検索クエリ]\n{queries}"
        )

    async def _review_ai_answer_if_needed(
        self,
        payload: discord.RawReactionActionEvent,
        context: AiAnswerReviewContext,
    ) -> None:
        if not hasattr(self, "_ai_answer_reviews_in_progress"):
            self._ai_answer_reviews_in_progress = set()
        if payload.message_id in self._ai_answer_reviews_in_progress:
            return
        self._ai_answer_reviews_in_progress.add(payload.message_id)
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None and hasattr(self.bot, "fetch_channel"):
                try:
                    channel = await self.bot.fetch_channel(payload.channel_id)
                except Exception:
                    channel = None
            if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
                return
            prompt = self._build_ai_review_prompt(context)
            model_name = context.model_name or self._current_chat_model_name()
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self.bot.ollama_client.chat_simple,
                    model=model_name,
                    prompt=prompt,
                    stream=False,
                    format="json",
                ),
                timeout=max(20, self._cfg_ai_timeout()),
            )
            payload_json = self._parse_json_payload(raw or "")
            if not isinstance(payload_json, dict):
                return
            needs_resend = bool(payload_json.get("needs_resend"))
            revised_answer = strip_ansi_and_ctrl(str(payload_json.get("revised_answer") or "")).strip()
            if not needs_resend or not revised_answer:
                try:
                    source = await channel.fetch_message(payload.message_id)
                    await source.add_reaction("✅")
                except Exception:
                    logger.debug("Failed to mark AI review as ok", exc_info=True)
                return
            prefix = ""
            if context.question_author_id:
                prefix = f"<@{context.question_author_id}>\n"
            sent = await self._send_ai_text_response(
                channel,
                revised_answer,
                prefix=prefix,
                source_msg=None,
                question_text=context.question_text,
                model_name=model_name,
                references=list(context.references),
                reference_details=list(context.reference_details),
                web_queries=list(context.web_queries),
            )
            if sent:
                self._ai_answer_reviews[sent[0].id] = AiAnswerReviewContext(
                    guild_id=context.guild_id,
                    channel_id=context.channel_id,
                    question_message_id=context.question_message_id,
                    question_author_id=context.question_author_id,
                    question_text=context.question_text,
                    answer_text=revised_answer,
                    model_name=model_name,
                    references=context.references,
                    reference_details=context.reference_details,
                    web_queries=context.web_queries,
                )
        except Exception:
            logger.exception("AI answer review failed")
        finally:
            self._ai_answer_reviews_in_progress.discard(payload.message_id)

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
                self._get_bot_game_catalog(),
                self._build_rag_context(
                    f"{normalized_query}\n機能一覧 できること 使い方 コマンド",
                    limit=12,
                    capability_only=True,
                    body_limit=None,
                    guild_id=(
                        source_msg.guild.id
                        if source_msg is not None and hasattr(source_msg, "guild") and source_msg.guild is not None
                        else getattr(getattr(channel, "guild", None), "id", 0) or 0
                    ),
                    channel_id=channel_id,
                ),
                self._build_rag_context(
                    normalized_query,
                    limit=6,
                    capability_only=False,
                    body_limit=None,
                    guild_id=(
                        source_msg.guild.id
                        if source_msg is not None and hasattr(source_msg, "guild") and source_msg.guild is not None
                        else getattr(getattr(channel, "guild", None), "id", 0) or 0
                    ),
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
        prompt = self._safe_prompt_format(
            get_prompt("chat", "capability_prompt"),
            channel_profile_block=channel_profile_block,
            query=normalized_query,
            rag_context=rag_context,
            updates_block=(f"[最新更新(git log)]\n{updates}\n" if updates else ""),
        )
        references = self._collect_reference_labels(channel_profile_block, rag_context, updates)
        model_name = self._current_chat_model_name()
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
                or "不明です。"
            )
            prefix = f"{mention}\n" if mention else ""
            await self._send_ai_text_response(
                channel,
                answer,
                prefix=prefix,
                source_msg=source_msg,
                question_text=query,
                model_name=model_name,
                references=references,
            )
            if source_msg is not None:
                await self._log_bot_activity_event(
                    source_msg,
                    kind="メンション",
                    processing="機能説明",
                    input_text=query,
                    output_text=answer,
                    title="Bot 管理ログ",
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
                            model=self._current_chat_model_name(),
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
                    title="Bot 管理ログ",
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

    def _kenny_chat_delete_mirrors_on_source_delete(self) -> bool:
        return bool(_settings.get("kenny_chat.delete_mirrors_on_source_delete", False))

    def _forget_kenny_chat_mirrors(self, message_id: int) -> list[tuple[int, int]]:
        mirrors = self._kenny_chat_mirrors.pop(message_id, [])
        for _ch_id, mirror_id in mirrors:
            self._kenny_chat_reverse.pop(mirror_id, None)
        return mirrors

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
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != get_reaction_emoji(AI_REVIEW_EMOJI):
            return
        if payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        context = self._ai_answer_reviews.get(payload.message_id)
        if context is None:
            return
        await self._review_ai_answer_if_needed(payload, context)

    @commands.Cog.listener()
    async def on_message_delete(self, msg: discord.Message):
        """kenny-chat の元発言が削除されたら中継先も削除"""
        if msg.author.bot or not self._is_kenny_chat(msg):
            return

        mirrors = self._forget_kenny_chat_mirrors(msg.id)
        if not self._kenny_chat_delete_mirrors_on_source_delete():
            return

        for ch_id, m_id in mirrors:
            ch = self.bot.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.get_partial_message(m_id).delete()
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """キャッシュ外削除でも中継先を削除"""
        mirrors = self._forget_kenny_chat_mirrors(payload.message_id)
        if not self._kenny_chat_delete_mirrors_on_source_delete():
            return

        for ch_id, m_id in mirrors:
            ch = self.bot.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.get_partial_message(m_id).delete()
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        """メッセージイベント（リアクション＆会話）"""
        # Bot自身のメッセージは無視
        if self.bot.user and msg.author.id == self.bot.user.id:
            return

        message_id = getattr(msg, "id", 0)
        if not self._claim_message_once(message_id):
            logger.info("Skipped duplicate message handling for message_id=%s", message_id)
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

        spam_guard_disabled = ModActions.should_disable_spam_guard(self.bot, msg.guild)
        if spam_guard_disabled and msg.guild is not None and msg.guild.id not in self._spam_guard_disabled_guilds:
            self._spam_guard_disabled_guilds.add(msg.guild.id)
            logger.warning(
                "Spam guard disabled for guild %s because bot lacks kick/ban permissions.",
                msg.guild.id,
            )

        # @everyone / @here スパム検出
        guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        if not spam_guard_disabled and self._is_everyone_mention(msg, content):
            violation = guard.record_everyone_mention(
                guild_id=msg.guild.id,
                user_id=msg.author.id,
                channel_id=msg.channel.id,
                message_id=msg.id,
            )
            if violation is not None:
                await self._handle_everyone_mention_violation(msg, violation)
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
        has_direct_mentions = bool(getattr(msg, "mentions", None) or getattr(msg, "role_mentions", None))

        if not spam_guard_disabled and (has_direct_mentions or is_reply_to_bot):
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

        # メンション / リプライがない場合はリアクションのみ
        if not should_treat_as_mention:
            normalized_text = normalize_user_text(content)
            if normalized_text and self._is_authoritative_correction_author(getattr(msg, "author", None)):
                sanitized_text = self._sanitize_for_prompt(
                    normalized_text,
                    self._cfg_int("security.max_user_message_chars", 1200),
                )
                if self._is_fix_request_report(sanitized_text):
                    if not await self._reject_if_ai_rate_limited(
                        msg,
                        spam_guard_disabled=spam_guard_disabled,
                    ):
                        try:
                            await self._log_fix_request(msg, sanitized_text)
                        except Exception:
                            logger.debug("Failed to log authoritative correction", exc_info=True)
            user_name = msg.author.display_name or msg.author.name or str(msg.author.id)
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
            for keyword, emoji in get_keyword_reactions(guild_id=msg.guild.id).items():
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
        image_attachments = self._image_attachments(msg)
        if not text and image_attachments:
            text = "この画像を説明して"
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
        text = self._sanitize_for_prompt(
            text,
            self._cfg_int("security.max_user_message_chars", 1200),
        )

        is_fix_request = self._is_fix_request_report(text)
        repair_rate_limit_checked = False
        if is_fix_request:
            if await self._reject_if_ai_rate_limited(
                msg,
                spam_guard_disabled=spam_guard_disabled,
                should_treat_as_mention=should_treat_as_mention,
            ):
                await self.bot.process_commands(msg)
                return
            repair_rate_limit_checked = True
            try:
                await self._log_fix_request(msg, text)
            except Exception:
                logger.debug("Failed to log fix request", exc_info=True)

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
                title="Bot 管理ログ",
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
                    title="Bot 管理ログ",
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
                title="Bot 管理ログ",
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

        if (
            self._is_server_owner_query(text)
            or self._is_member_count_query(text)
            or self._is_top_talker_query(text)
        ):
            await self._answer_server_stats_query(
                msg.channel,
                text,
                mention=msg.author.mention,
                source_msg=msg,
            )
            self._arm_recent_mention_window(msg)
            await self.bot.process_commands(msg)
            return

        if (
            not image_attachments
            and self._is_channel_profile_query(text)
            and not self._is_mentioned_person_lookup_query(msg, text)
        ):
            await self._answer_channel_profile_query(
                msg.channel,
                text,
                mention=msg.author.mention,
                source_msg=msg,
                channel_id=msg.channel.id,
            )
            self._arm_recent_mention_window(msg)
            await self.bot.process_commands(msg)
            return

        if _looks_like_image_generation_request(text):
            await self._handle_image_generation_request(msg, text)
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
        if not repair_rate_limit_checked:
            if await self._reject_if_ai_rate_limited(
                msg,
                spam_guard_disabled=spam_guard_disabled,
                should_treat_as_mention=should_treat_as_mention,
            ):
                await self.bot.process_commands(msg)
                return

        # =========================
        # Schedule embedding indexing
        # =========================
        self._schedule_message_index(
            guild_id=msg.guild.id,
            channel_id=msg.channel.id,
            message_id=msg.id,
            author_id=msg.author.id,
            author=user_name,
            content=text,
        )

        references: list[str] = []
        reference_details: list[str] = []
        today_local = now_jst()
        absolute_date = today_local.strftime("%Y-%m-%d")
        absolute_datetime = today_local.strftime("%Y-%m-%d %H:%M:%S JST")
        history_context, planned_refs, web_queries, planned_details, direct_web_answer = await self._resolve_chat_context(
            msg=msg,
            user_display=user_display,
            text=text,
        )
        references.extend(planned_refs)
        reference_details.extend(planned_details)
        if direct_web_answer and not image_attachments:
            await self._send_ai_text_response(
                msg.channel,
                direct_web_answer,
                prefix=f"{msg.author.mention}\n",
                source_msg=msg,
                question_text=text,
                model_name="web_search",
                references=references,
                reference_details=reference_details,
                web_queries=web_queries,
            )
            log_ai_output(
                msg.author,
                response=direct_web_answer,
                model="web_search",
                msg=msg,
                references=references,
                reference_details=reference_details,
                web_queries=web_queries,
            )
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="Web検索",
                input_text=text,
                output_text=direct_web_answer,
                model_name="web_search",
                title="Bot 管理ログ",
                description="Web 検索応答を送信しました。",
                references=references,
                reference_details=reference_details,
                web_queries=web_queries,
            )
            self._arm_recent_mention_window(msg)
            await self.bot.process_commands(msg)
            return
        if (
            self._needs_web_search_for_accuracy(text)
            and not image_attachments
            and not self._has_web_search_context(references)
        ):
            await self._handle_current_info_search_failure(
                msg.channel,
                mention=msg.author.mention,
                query=text,
                source_msg=msg,
                model_name="web_search",
                references=references,
            )
            self._arm_recent_mention_window(msg)
            await self.bot.process_commands(msg)
            return
        progress_key = f"ai-progress:{msg.channel.id}:{msg.author.id}"
        model_name = self._current_chat_model_name()
        ticket = await self.bot.ai_progress_tracker.create_ticket()
        recent_image_context = (
            self._recent_image_context_block(msg)
            if not image_attachments and self._should_use_recent_image_context(text)
            else ""
        )
        combined_history_context = "\n\n".join(
            part for part in (history_context, recent_image_context) if part.strip()
        )
        tool_queries: list[str] = []
        tool_references: list[str] = []
        prompt = PROMPT_TEMPLATE.format(
            user_display=user_display,
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
        image_payloads: list[tuple[bytes, str]] = []
        image_labels: list[str] = []
        if image_attachments:
            image_payloads, image_labels = await self._read_image_attachments(msg)
            if not image_payloads:
                await msg.channel.send(
                    f"{msg.author.mention}\n画像を読み取れませんでした。サイズを小さくしてもう一度送ってください。",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._arm_recent_mention_window(msg)
                await self.bot.process_commands(msg)
                return
            references.extend([f"attachment:{label}" for label in image_labels])

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
                if image_payloads:
                    image_prompt = self._build_image_analysis_prompt(text, user_display)
                    answer = await self._run_image_analysis(
                        model=model_name,
                        chat_messages=chat_messages,
                        prompt=image_prompt,
                        images=image_payloads,
                    )
                else:
                    answer, tool_references, tool_queries, tool_reference_details = await self._run_ollama_chat_with_tools(
                        model=model_name,
                        messages=chat_messages,
                        tools=[],
                        guild=msg.guild,
                        channel_id=msg.channel.id,
                        user_id=msg.author.id,
                    )
                    reference_details.extend(tool_reference_details)
            finally:
                await self.bot.ai_progress_tracker.release(ticket)

            answer = strip_ansi_and_ctrl((answer or "").strip())
            if not answer:
                answer = "不明です。"
            answer = self._sanitize_user_visible_answer(answer)
            if image_payloads:
                self._remember_image_context(msg, answer)

            # 応答文字数制限（メンション部分を考慮：メンション約25文字 + 改行）
            max_len = self._cfg_int("chat.max_response_length", 1800)
            if len(answer) > max_len:
                answer = answer[:max_len] + "\n...(省略)..."

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

            if self._should_send_letter_file(text):
                await self._send_letter_file(msg, answer_with_refs)
            else:
                await self._send_ai_text_response(
                    msg.channel,
                    answer_with_refs,
                    prefix=f"{msg.author.mention}\n",
                    source_msg=msg,
                    question_text=text,
                    model_name=model_name,
                    references=references,
                    reference_details=reference_details,
                    web_queries=web_queries + tool_queries,
                )
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="画像解析" if image_payloads else ("修正モード応答" if is_fix_request else "通常会話"),
                codex_mode=is_fix_request,
                input_text=f"{text}\n[画像: {', '.join(image_labels)}]" if image_labels else text,
                output_text=answer_with_refs,
                model_name=model_name,
                title="Bot 管理ログ",
                description=(
                    "修正依頼を受けた会話応答を送信しました。"
                    if is_fix_request
                    else "添付画像への AI 解析応答を送信しました。"
                    if image_payloads
                    else "メンションまたはリプライへの AI 応答を送信しました。"
                ),
                references=references,
                reference_details=reference_details,
                web_queries=web_queries + tool_queries,
            )
            self._arm_recent_mention_window(msg)

        except Exception as e:
            logger.exception("AI response failed")
            await self._log_bot_activity_event(
                msg,
                kind="メンション",
                processing="画像解析" if image_payloads else ("修正モード応答" if is_fix_request else "通常会話"),
                codex_mode=is_fix_request,
                level="error",
                title="Bot 管理ログ",
                description=(
                    "修正依頼を受けた会話応答に失敗しました。"
                    if is_fix_request
                    else "添付画像への AI 解析応答に失敗しました。"
                    if image_payloads
                    else "メンションまたはリプライへの AI 応答に失敗しました。"
                ),
                input_text=f"{text}\n[画像: {', '.join(image_labels)}]" if image_labels else text,
                error_text=str(e),
                model_name=model_name,
                references=references,
                reference_details=reference_details,
                web_queries=web_queries + tool_queries,
            )
            if isinstance(e, asyncio.TimeoutError):
                model_name = self._current_chat_model_name()
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
