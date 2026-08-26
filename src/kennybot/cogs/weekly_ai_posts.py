from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from src.kennybot.utils.paths import RUNTIME_STATE_DIR
from src.kennybot.utils.reactions import get_reaction_emoji
from src.kennybot.utils.runtime_settings import get_settings


logger = logging.getLogger(__name__)

WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "月": 0,
    "月曜": 0,
    "月曜日": 0,
    "tue": 1,
    "tuesday": 1,
    "火": 1,
    "火曜": 1,
    "火曜日": 1,
    "wed": 2,
    "wednesday": 2,
    "水": 2,
    "水曜": 2,
    "水曜日": 2,
    "thu": 3,
    "thursday": 3,
    "木": 3,
    "木曜": 3,
    "木曜日": 3,
    "fri": 4,
    "friday": 4,
    "金": 4,
    "金曜": 4,
    "金曜日": 4,
    "sat": 5,
    "saturday": 5,
    "土": 5,
    "土曜": 5,
    "土曜日": 5,
    "sun": 6,
    "sunday": 6,
    "日": 6,
    "日曜": 6,
    "日曜日": 6,
}


def _parse_weekday(value: Any) -> int | None:
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if 0 <= number <= 6 else None
    return WEEKDAY_ALIASES.get(text)


def _is_daily_schedule(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"daily", "everyday", "every day", "毎日", "まいにち"}


def _parse_hhmm(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _due_marker(now: datetime, *, weekday: int | None, hour: int, minute: int) -> str | None:
    if weekday is not None and now.weekday() != weekday:
        return None
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < scheduled:
        return None
    return now.date().isoformat()


def _safe_post_text(value: str, *, max_chars: int = 1900) -> str:
    lines = [" ".join(line.split()) for line in (value or "").splitlines()]
    text = "\n".join(lines).strip()
    text = text.replace("@", "@\u200b")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _recent_post_summary(value: str, *, max_chars: int = 180) -> str:
    lines = [" ".join(line.split()) for line in (value or "").splitlines()]
    text = " / ".join(line for line in lines if line).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _reaction_emojis(item: dict[str, Any]) -> list[str]:
    language_section = item.get("today_language")
    if not isinstance(language_section, dict):
        return []
    reactions = language_section.get("reactions")
    if not isinstance(reactions, dict) or not bool(reactions.get("enabled", False)):
        return []
    emojis = [
        str(reactions.get("unknown_emoji") or get_reaction_emoji("weekly_today_language.unknown")),
        str(reactions.get("known_emoji") or get_reaction_emoji("weekly_today_language.known")),
        str(reactions.get("learned_emoji") or get_reaction_emoji("weekly_today_language.learned")),
        str(reactions.get("issue_emoji") or get_reaction_emoji("weekly_today_language.issue")),
    ]
    return [emoji for emoji in emojis if emoji.strip()]


class WeeklyAiPosts(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._task: asyncio.Task[None] | None = None
        self._state_path = RUNTIME_STATE_DIR / "weekly_ai_posts.json"
        self._state: dict[str, Any] = self._load_state()

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._run_loop())

    def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            logger.exception("Failed to load weekly AI post state")
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(f"{self._state_path}.tmp")
        tmp_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self._state_path)

    async def _run_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._process_due_posts()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Weekly AI post loop failed")
            poll_seconds = int(get_settings().get("weekly_ai_posts.poll_seconds", 60) or 60)
            await asyncio.sleep(max(15, min(poll_seconds, 3600)))

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                logger.exception("Failed to fetch weekly AI post channel: %s", channel_id)
                return None
        if isinstance(channel, discord.abc.Messageable):
            return channel
        return None

    def _item_id(self, item: dict[str, Any]) -> str:
        raw_id = str(item.get("id") or "").strip()
        if raw_id:
            return raw_id
        return f"{item.get('channel_id')}:{item.get('weekday')}:{item.get('time')}"

    def _content_key(self, item: dict[str, Any]) -> str:
        payload = {
            "weekday": item.get("weekday"),
            "time": item.get("time"),
            "timezone": item.get("timezone") or "Asia/Tokyo",
            "model": item.get("model"),
            "prompt": item.get("prompt"),
            "system_prompt": item.get("system_prompt"),
            "today_language": item.get("today_language"),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _recent_posts_for_prompt(self, item_id: str, *, limit: int) -> list[str]:
        state_item = self._state.get(item_id)
        if not isinstance(state_item, dict):
            return []
        posts = state_item.get("recent_posts")
        if not isinstance(posts, list):
            return []
        summaries: list[str] = []
        for entry in reversed(posts):
            if not isinstance(entry, dict):
                continue
            summary = str(entry.get("summary") or "").strip()
            if summary:
                summaries.append(summary)
            if len(summaries) >= limit:
                break
        return summaries

    def _remember_sent_post(self, item_id: str, *, marker: str, sent_at: str, text: str, limit: int) -> None:
        state_item = self._state.setdefault(item_id, {})
        if not isinstance(state_item, dict):
            state_item = {}
            self._state[item_id] = state_item
        posts = state_item.get("recent_posts")
        if not isinstance(posts, list):
            posts = []
        summary = _recent_post_summary(text)
        posts.append({"date": marker, "sent_at": sent_at, "summary": summary})
        state_item["recent_posts"] = posts[-max(1, limit) :]

    def _build_prompt(self, item: dict[str, Any], recent_posts: list[str] | None = None) -> str:
        prompt = str(item.get("prompt") or "").strip()
        if recent_posts:
            recent_block = "\n".join(f"- {summary}" for summary in recent_posts)
            prompt = "\n\n".join(
                part
                for part in (
                    prompt,
                    (
                        "重複回避:\n"
                        "最近投稿した豆知識は以下です。同じ国・地域・文化圏・人物・料理・祭り・語源・"
                        "出来事など、中心題材が同じネタは避けてください。言い換えだけの再投稿も禁止です。\n"
                        f"{recent_block}"
                    ),
                )
                if part.strip()
            )
        language_section = item.get("today_language")
        if isinstance(language_section, dict) and bool(language_section.get("enabled", False)):
            prompt = "\n\n".join(
                part
                for part in (
                    prompt,
                    (
                        "追加で、豆知識の下に今日の言語セクションも付けてください。\n"
                        "条件:\n"
                        "- 見出しは必ず「【今日の言語】~〇〇語~」の形式にする\n"
                        "- 言語は、海外豆知識で扱った国・地域・文化圏に関連する言語を1つ選ぶ\n"
                        "- 基礎フレーズまたは単語を3つ出す\n"
                        "- 各行は「言語の表記（カタカナ読み）: 意味（日本語）」の形式にする\n"
                        "- 例: Hola（オラ）: こんにちは\n"
                        "- 豆知識と今日の言語は別セクションにして、「【今日の言語】」の上に空行を1つ入れる\n"
                        "- 最後に「知らなかったよって人は ✋、知ってた単語や豆知識があれば 👀、覚えたら ✅、問題や間違いがありそうなら ⚠️ を押してくださいね。」と書く"
                    ),
                )
                if part.strip()
            )
        system_prompt = str(
            item.get("system_prompt")
            or "Discordに投稿する本文だけを返してください。前置き、説明、引用符、Markdownのコードブロックは不要です。"
        ).strip()
        return f"{system_prompt}\n\n{prompt}".strip()

    async def _generate_text(self, item: dict[str, Any], *, recent_posts: list[str] | None = None) -> str | None:
        settings = get_settings()
        model = str(item.get("model") or settings.get("ai.models.chat", "") or "").strip()
        prompt = self._build_prompt(item, recent_posts=recent_posts)
        if not model or not prompt:
            return None
        fallback_models = [
            str(settings.get("ai.models.default", "") or "").strip(),
            str(settings.get("ai.models.summary", "") or "").strip(),
        ]
        fallback_models = [value for value in fallback_models if value and value != model]
        return await asyncio.to_thread(
            self.bot.ollama_client.chat_simple,
            model,
            prompt,
            False,
            fallback_models,
        )

    async def _process_due_posts(self) -> None:
        settings = get_settings()
        if not bool(settings.get("weekly_ai_posts.enabled", False)):
            return
        items = settings.get("weekly_ai_posts.items", [])
        if not isinstance(items, list):
            return
        generated_text_by_key: dict[str, str] = {}

        for item in items:
            if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                continue
            item_id = self._item_id(item)
            channel_id = int(item.get("channel_id") or 0)
            weekday = None if _is_daily_schedule(item.get("weekday")) else _parse_weekday(item.get("weekday"))
            hhmm = _parse_hhmm(item.get("time"))
            timezone_name = str(item.get("timezone") or "Asia/Tokyo").strip()
            if not item_id or not channel_id or (weekday is None and not _is_daily_schedule(item.get("weekday"))) or hhmm is None:
                logger.warning("Invalid weekly AI post item: %r", item)
                continue

            try:
                now = datetime.now(ZoneInfo(timezone_name))
            except Exception:
                logger.warning("Invalid weekly AI post timezone: %s", timezone_name)
                now = datetime.now(ZoneInfo("Asia/Tokyo"))

            marker = _due_marker(now, weekday=weekday, hour=hhmm[0], minute=hhmm[1])
            if marker is None:
                continue
            state_item = self._state.setdefault(item_id, {})
            if isinstance(state_item, dict) and (
                state_item.get("last_sent_date") == marker
                or state_item.get("last_attempt_date") == marker
            ):
                continue

            channel = await self._resolve_channel(channel_id)
            if channel is None:
                continue
            state_item = self._state.setdefault(item_id, {})
            if not isinstance(state_item, dict):
                state_item = {}
                self._state[item_id] = state_item
            state_item["last_attempt_date"] = marker
            state_item["last_attempt_at"] = now.isoformat()
            self._save_state()
            content_key = self._content_key(item)
            recent_history_limit = int(settings.get("weekly_ai_posts.recent_history_limit", 12) or 12)
            recent_history_limit = max(1, min(int(item.get("recent_history_limit") or recent_history_limit), 50))
            if content_key in generated_text_by_key:
                text = generated_text_by_key[content_key]
            else:
                recent_posts = self._recent_posts_for_prompt(item_id, limit=recent_history_limit)
                text = _safe_post_text(await self._generate_text(item, recent_posts=recent_posts) or "")
                if text:
                    generated_text_by_key[content_key] = text
            if not text:
                logger.warning("Weekly AI post generated empty text: %s", item_id)
                continue
            try:
                sent = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
                for emoji in _reaction_emojis(item):
                    try:
                        await sent.add_reaction(emoji)
                    except Exception:
                        logger.debug("Failed to add weekly AI post reaction: %s", emoji, exc_info=True)
                state_item = self._state.setdefault(item_id, {})
                if not isinstance(state_item, dict):
                    state_item = {}
                    self._state[item_id] = state_item
                state_item["last_attempt_date"] = marker
                state_item["last_attempt_at"] = now.isoformat()
                state_item["last_sent_date"] = marker
                state_item["last_sent_at"] = now.isoformat()
                self._remember_sent_post(
                    item_id,
                    marker=marker,
                    sent_at=now.isoformat(),
                    text=text,
                    limit=recent_history_limit,
                )
                self._save_state()
                logger.info("Sent weekly AI post: %s channel=%s", item_id, channel_id)
            except Exception:
                logger.exception("Failed to send weekly AI post: %s", item_id)
