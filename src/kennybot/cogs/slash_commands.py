# cogs/slash_commands.py
# スラッシュコマンド集

import asyncio
import io
import json
import logging
import os
import random
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse
import wave
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.utils.build_info import load_build_info
from src.kennybot.utils.app_constants import MOD_PANEL_CHANNEL_ID
from src.kennybot.utils.command_catalog import (
    COMMAND_CATEGORY_ORDER,
    HELP_SECTIONS,
    SLASH_COMMANDS,
    get_slash_command_meta,
)
from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.message_logger import log_system_event, log_codex_repair_mode
from src.kennybot.utils.countdown import ChannelCountdown
from src.kennybot.utils.config import get_app_config
from src.kennybot.utils.reactions import get_reaction_emoji, reaction_aliases
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.text import sanitize_user_visible_error
from src.kennybot.utils.time import JST, now_jst
from src.kennybot.utils.vrchat_user import (
    VRChatAuthError,
    VRChatTwoFactorRequired,
    format_vrchat_user,
    get_vrchat_user_from_url,
)
from src.kennybot.utils.vrchat_world import format_vrchat_world_lines, search_vrchat_worlds
from src.kennybot.ai.client import create_ollama_client
from src.kennybot.utils.prompts import get_prompt

_settings = get_settings()
logger = logging.getLogger(__name__)
ReadableChannel = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread
HELP_META = get_slash_command_meta("help")
VC_CONTROL_META = get_slash_command_meta("vc_control")
BOT_INFO_META = get_slash_command_meta("bot_info")
PING_META = get_slash_command_meta("ping")
SUMMARIZE_RECENT_META = get_slash_command_meta("summarize_recent")
EXPORT_CHANNEL_MESSAGES_META = get_slash_command_meta("export_channel_messages")
SET_RECENT_WINDOW_META = get_slash_command_meta("set_recent_window")
CONFIG_META = get_slash_command_meta("config")
MODEL_LIST_META = get_slash_command_meta("model_list")
MODEL_CHANGE_META = get_slash_command_meta("model_change")
REACTION_ROLE_SET_META = get_slash_command_meta("reaction_role_set")
REACTION_ROLE_REMOVE_META = get_slash_command_meta("reaction_role_remove")
REACTION_ROLE_LIST_META = get_slash_command_meta("reaction_role_list")
MINUTES_META = get_slash_command_meta("minutes")
TIMER_META = get_slash_command_meta("timer")
GROUP_MATCH_META = get_slash_command_meta("group_match")
MOD_PANEL_META = get_slash_command_meta("modpanel")
VRCHAT_WORLD_META = get_slash_command_meta("vrchat_world")
VRC_USER_META = get_slash_command_meta("vrc_user")


@dataclass
class VcPanelState:
    guild_id: int
    channel_id: int
    voice_channel_id: int
    host_user_id: int
    joined_user_ids: set[int] = field(default_factory=set)


@dataclass
class GroupMatchState:
    guild_id: int
    channel_id: int
    host_user_id: int
    group_size: int
    visibility: str = "public"
    title: str | None = None


@dataclass
class MinutesControlState:
    guild_id: int
    channel_id: int
    message_id: int
    status_message_id: int
    owner_user_id: int
    voice_channel_id: int


class SlashCommands(commands.Cog):
    """スラッシュコマンド"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._started_at = discord.utils.utcnow()
        # message_id -> (seconds, title)
        self._timer_restart_templates: dict[int, tuple[int, str]] = {}
        # message_id -> vc panel state
        self._vc_panels: dict[int, VcPanelState] = {}
        # message_id -> group match state
        self._group_matches: dict[int, GroupMatchState] = {}
        # message_id -> meeting control panel state
        self._minutes_panels: dict[int, MinutesControlState] = {}
        self._countdowns = ChannelCountdown()

    def _clear_minutes_panels(self, guild_id: int) -> None:
        for message_id, state in list(self._minutes_panels.items()):
            if state.guild_id == guild_id:
                self._minutes_panels.pop(message_id, None)

    def _cfg_int_list(self, path: str) -> list[int]:
        raw = _settings.get(path, [])
        if isinstance(raw, (list, tuple, set)):
            values = raw
        elif isinstance(raw, str):
            values = re.split(r"[\s,]+", raw.strip()) if raw.strip() else []
        else:
            values = []
        out: list[int] = []
        for value in values:
            try:
                out.append(int(value))
            except Exception:
                continue
        return out

    def _is_configured_admin_user(self, user_id: int) -> bool:
        return user_id in set(self._cfg_int_list("admin.authoritative_correction_user_ids"))

    def _is_admin_like(self, interaction: discord.Interaction) -> bool:
        user = getattr(interaction, "user", None)
        if user is None:
            return False
        if self._is_configured_admin_user(int(getattr(user, "id", 0) or 0)):
            return True
        permissions = getattr(user, "guild_permissions", None)
        return bool(getattr(permissions, "administrator", False))

    async def _ensure_admin_like(self, interaction: discord.Interaction) -> bool:
        if self._is_admin_like(interaction):
            return True
        await interaction.response.send_message("この操作は管理者のみ実行できます。", ephemeral=True)
        return False

    async def _set_minutes_status_ended(self, guild: discord.Guild) -> None:
        for panel in list(self._minutes_panels.values()):
            if panel.guild_id != guild.id:
                continue
            await self._update_minutes_status_message(guild, panel, ended=True)

    async def _find_message_for_reaction_role(
        self,
        guild: discord.Guild,
        message_id: int,
        preferred_channel: discord.abc.Messageable | None = None,
    ) -> discord.Message | None:
        channels: list[discord.abc.Messageable] = []
        seen_channel_ids: set[int] = set()

        def add_channel(channel: object | None) -> None:
            channel_id = getattr(channel, "id", None)
            if channel is None or not isinstance(channel_id, int) or channel_id in seen_channel_ids:
                return
            if not hasattr(channel, "fetch_message"):
                return
            seen_channel_ids.add(channel_id)
            channels.append(channel)  # type: ignore[arg-type]

        add_channel(preferred_channel)
        for channel in guild.text_channels:
            add_channel(channel)
        for thread in guild.threads:
            add_channel(thread)

        for channel in channels:
            try:
                return await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden):
                continue
            except discord.HTTPException:
                continue
        return None

    @property
    def VC_JOIN_EMOJI(self) -> str:
        return get_reaction_emoji("vc.join")

    @property
    def VC_MUTE_ON_EMOJI(self) -> str:
        return get_reaction_emoji("vc.mute_on")

    @property
    def VC_MUTE_OFF_EMOJI(self) -> str:
        return get_reaction_emoji("vc.mute_off")

    @property
    def VC_DEAF_ON_EMOJI(self) -> str:
        return get_reaction_emoji("vc.deaf_on")

    @property
    def VC_DEAF_OFF_EMOJI(self) -> str:
        return get_reaction_emoji("vc.deaf_off")

    @property
    def GROUP_MATCH_EMOJI(self) -> str:
        return get_reaction_emoji("group_match.join")

    @property
    def GROUP_MATCH_START_EMOJI(self) -> str:
        return get_reaction_emoji("group_match.start")

    @property
    def MINUTES_SUMMARY_EMOJI(self) -> str:
        return get_reaction_emoji("minutes.summary")

    @property
    def MINUTES_STOP_EMOJI(self) -> str:
        return get_reaction_emoji("minutes.stop")

    @property
    def MINUTES_PLAYBACK_EMOJI(self) -> str:
        return get_reaction_emoji("minutes.playback")

    @property
    def MINUTES_REALTIME_EMOJI(self) -> str:
        return get_reaction_emoji("minutes.realtime")

    @property
    def TIMER_RESTART_EMOJI(self) -> str:
        return get_reaction_emoji("timer.restart")
    _CONFIG_CHOICES = [
        app_commands.Choice(name="会話履歴の参照行数", value="chat.history_lines"),
        app_commands.Choice(name="本人履歴の参照行数", value="chat.user_history_lines"),
        app_commands.Choice(name="全体履歴の参照行数", value="chat.channel_history_lines"),
        app_commands.Choice(name="履歴保存の最大件数", value="chat.history_max_messages"),
        app_commands.Choice(name="履歴保存日数", value="chat.history_retention_days"),
        app_commands.Choice(name="返信の最大文字数", value="chat.max_response_length"),
        app_commands.Choice(name="プロンプト文字数上限", value="chat.max_response_length_prompt"),
        app_commands.Choice(name="kenny-chat発言クールダウン秒", value="kenny_chat.cooldown_seconds"),
        app_commands.Choice(name="要約の既定件数", value="summarize_recent_default_messages"),
        app_commands.Choice(name="要約の履歴取得件数", value="summarize_recent.history_fetch_limit"),
        app_commands.Choice(name="要約の投入行数上限", value="summarize_recent.transcript_lines_limit"),
        app_commands.Choice(name="要約の最大件数", value="summarize_recent.max_messages"),
        app_commands.Choice(name="既定モデル", value="ai.models.default"),
        app_commands.Choice(name="会話モデル", value="ai.models.chat"),
        app_commands.Choice(name="埋め込みモデル", value="ai.models.embedding"),
        app_commands.Choice(name="要約モデル", value="ai.models.summary"),
        app_commands.Choice(name="モデル応答タイムアウト秒", value="ai.timeout_sec"),
        app_commands.Choice(name="議事録リアルタイム翻訳", value="meeting.realtime_translation_enabled"),
        app_commands.Choice(name="議事録文字起こしプロバイダ", value="meeting.transcription_provider"),
        app_commands.Choice(name="Google STT 言語コード", value="meeting.google_language_code"),
        app_commands.Choice(name="管理者訂正ユーザーID一覧", value="admin.authoritative_correction_user_ids"),
        app_commands.Choice(name="同時実行数", value="security.ai_max_concurrency"),
        app_commands.Choice(name="チャンネル間隔秒", value="security.ai_channel_cooldown_seconds"),
        app_commands.Choice(name="入力最大文字数", value="security.max_user_message_chars"),
        app_commands.Choice(name="kenny-chat招待URL/全体メンション禁止", value="kenny_chat.block_invite_and_mass_mention"),
        app_commands.Choice(name="スパム許容メッセージ数", value="spam.max_msgs"),
        app_commands.Choice(name="スパム判定秒数", value="spam.per_seconds"),
    ]

    _INT_KEYS = {
        "chat.history_lines",
        "chat.user_history_lines",
        "chat.channel_history_lines",
        "chat.semantic_history_k",
        "chat.history_max_messages",
        "chat.history_retention_days",
        "chat.max_response_length",
        "chat.max_response_length_prompt",
        "kenny_chat.cooldown_seconds",
        "summarize_recent_default_messages",
        "summarize_recent.history_fetch_limit",
        "summarize_recent.transcript_lines_limit",
        "summarize_recent.max_messages",
        "ai.timeout_sec",
        "meeting.max_minutes",
        "meeting.audio_max_total_mb",
        "meeting.audio_max_user_mb",
        "meeting.google_chunk_seconds",
        "meeting.google_timeout_sec",
        "security.ai_max_concurrency",
        "security.ai_channel_cooldown_seconds",
        "security.max_user_message_chars",
        "spam.max_msgs",
        "spam.per_seconds",
        "spam.max_ai_calls",
        "spam.ai_per_seconds",
        "spam.dup_window_seconds",
        "spam.warn_cooldown_seconds",
    }
    _INT_LIST_KEYS = {
        "admin.authoritative_correction_user_ids",
    }
    _BOOL_KEYS = {
        "kenny_chat.block_invite_and_mass_mention",
        "meeting.realtime_translation_enabled",
    }
    _GROUP_SIZE_CHOICES = [
        app_commands.Choice(name="2人組", value=2),
        app_commands.Choice(name="3人組", value=3),
    ]
    _GROUP_VISIBILITY_CHOICES = [
        app_commands.Choice(name="公開", value="public"),
        app_commands.Choice(name="非公開", value="private"),
    ]
    _WHISPER_MODEL_CHOICES = [
        app_commands.Choice(name="whisper/tiny", value="whisper/tiny"),
        app_commands.Choice(name="whisper/base", value="whisper/base"),
        app_commands.Choice(name="whisper/small", value="whisper/small"),
        app_commands.Choice(name="whisper/medium", value="whisper/medium"),
        app_commands.Choice(name="whisper/large-v3-turbo", value="whisper/large-v3-turbo"),
        app_commands.Choice(name="moonshine/tiny-ja", value="moonshine/tiny-ja"),
        app_commands.Choice(name="moonshine/base-ja", value="moonshine/base-ja"),
    ]
    _OLLAMA_MODEL_TARGET_CHOICES = [
        app_commands.Choice(name="default", value="default"),
        app_commands.Choice(name="chat", value="chat"),
        app_commands.Choice(name="summary", value="summary"),
        app_commands.Choice(name="embedding", value="embedding"),
    ]

    def _autocomplete_config_keys(self, current: str) -> list[app_commands.Choice[str]]:
        query = (current or "").strip().lower()
        matches: list[app_commands.Choice[str]] = []
        for choice in self._CONFIG_CHOICES:
            haystack = f"{choice.name} {choice.value}".lower()
            if not query or query in haystack:
                matches.append(choice)
        return matches[:25]

    @staticmethod
    def _is_readable_channel(channel: object) -> bool:
        return isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread))

    @staticmethod
    def _emoji_key(emoji: object) -> str:
        name = getattr(emoji, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return str(emoji).strip()

    async def _handle_minutes_reaction(
        self,
        *,
        guild: discord.Guild | None,
        channel: discord.abc.Messageable | None,
        message_id: int,
        channel_id: int,
        guild_id: int | None,
        user_id: int,
        emoji: str,
    ) -> bool:
        logger.info(
            "minutes_reaction_handle_start message=%s channel=%s guild=%s user=%s emoji=%s channel_type=%s panel_registered=%s",
            message_id,
            channel_id,
            guild_id,
            user_id,
            emoji,
            type(channel).__name__ if channel is not None else "None",
            message_id in self._minutes_panels,
        )
        realtime_emojis = reaction_aliases(self.MINUTES_REALTIME_EMOJI)
        summary_emojis = reaction_aliases(self.MINUTES_SUMMARY_EMOJI)
        stop_emojis = reaction_aliases(self.MINUTES_STOP_EMOJI)
        playback_emojis = reaction_aliases(self.MINUTES_PLAYBACK_EMOJI)
        progress_text = {
            **{item: "リアル文字起こしを切り替えています..." for item in realtime_emojis},
            **{item: "途中要約中..." for item in summary_emojis},
            **{item: "録音を停止して文字起こし・要約中..." for item in stop_emojis},
            **{item: "文字起こし・要約を破棄して録音再生を準備中..." for item in playback_emojis},
        }.get(emoji)
        if progress_text is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.info(
                "minutes_reaction_ignored message=%s emoji_supported=%s channel_supported=%s",
                message_id,
                progress_text is not None,
                isinstance(channel, (discord.TextChannel, discord.Thread)),
            )
            return False

        minutes_panel = self._minutes_panels.get(message_id)
        if not minutes_panel:
            try:
                source_message = await channel.fetch_message(message_id)
                is_minutes_panel = bool(
                    self.bot.user
                    and source_message.author.id == self.bot.user.id
                    and "議事録を開始しました" in source_message.content
                )
            except Exception:
                is_minutes_panel = False
            if not is_minutes_panel:
                logger.info("minutes_reaction_not_panel message=%s", message_id)
                return False
            progress_message = await channel.send("議事録操作を受け付けました...")
            await progress_message.edit(
                content="この議事録操作パネルは無効です。Bot再起動後は `/minutes` で開始し直してください。"
            )
            asyncio.create_task(progress_message.delete(delay=10))
            return True
        logger.info(
            "minutes_panel_hit message=%s expected_channel=%s expected_guild=%s emoji=%s",
            message_id,
            minutes_panel.channel_id,
            minutes_panel.guild_id,
            emoji,
        )
        progress_message: discord.Message | None = None
        try:
            progress_message = await asyncio.wait_for(
                channel.send("議事録操作を受け付けました..."),
                timeout=3,
            )
            logger.info(
                "minutes_progress_sent message=%s progress_message=%s",
                message_id,
                progress_message.id,
            )
        except Exception:
            logger.exception(
                "minutes_progress_send_failed message=%s; continuing operation",
                message_id,
            )
        if guild_id != minutes_panel.guild_id or channel_id != minutes_panel.channel_id:
            logger.info(
                "minutes_panel_mismatch message=%s payload_channel=%s payload_guild=%s",
                message_id,
                channel_id,
                guild_id,
            )
            if progress_message is not None:
                await progress_message.edit(content="このチャンネルでは議事録を操作できません。")
                asyncio.create_task(progress_message.delete(delay=10))
            return True
        if guild is None:
            logger.info("minutes_panel_missing_guild message=%s guild=%s", message_id, guild_id)
            if progress_message is not None:
                await progress_message.edit(content="サーバー情報を取得できませんでした。もう一度試してください。")
                asyncio.create_task(progress_message.delete(delay=10))
            return True
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                logger.info("minutes_panel_missing_member message=%s user=%s", message_id, user_id)
                if progress_message is not None:
                    await progress_message.edit(content="操作したユーザー情報を取得できませんでした。")
                    asyncio.create_task(progress_message.delete(delay=10))
                return True
        if not isinstance(member, discord.Member):
            logger.info("minutes_panel_invalid_member message=%s user=%s", message_id, user_id)
            if progress_message is not None:
                await progress_message.edit(content="操作したユーザー情報が不正です。")
                asyncio.create_task(progress_message.delete(delay=10))
            return True
        if member.id != minutes_panel.owner_user_id and not member.guild_permissions.manage_guild:
            logger.info(
                "minutes_panel_no_permission message=%s user=%s owner=%s",
                message_id,
                user_id,
                minutes_panel.owner_user_id,
            )
            if progress_message is not None:
                await progress_message.edit(
                    content=f"{member.mention} この議事録を操作できるのは開始者またはサーバー管理者です。"
                )
                asyncio.create_task(progress_message.delete(delay=10))
            return True
        try:
            if progress_message is not None:
                await progress_message.edit(content=f"{member.mention} {progress_text}")
            logger.info("minutes_reaction_dispatch message=%s emoji=%s", message_id, emoji)
            if emoji in realtime_emojis:
                text = await self._toggle_minutes_realtime_mode(guild, member, minutes_panel)
                await channel.send(text, delete_after=10)
                return True
            if emoji in summary_emojis:
                text = await self._send_minutes_interim_summary(guild, channel)
                await channel.send(text, delete_after=10)
                return True
            if emoji in stop_emojis:
                text = await self._stop_minutes_session_from_panel(
                    guild,
                    channel,
                    member,
                    playback=False,
                    action="minutes_stop",
                )
                await channel.send(text, delete_after=10)
                return True
            if emoji in playback_emojis:
                text = await self._stop_minutes_session_from_panel(
                    guild,
                    channel,
                    member,
                    playback=True,
                    action="minutes_stop",
                )
                await channel.send(text, delete_after=10)
                return True
        except Exception as e:
            logger.exception("Meeting minutes panel operation failed")
            await channel.send(
                f"議事録操作に失敗しました: {sanitize_user_visible_error(e, max_chars=180)}",
                delete_after=10,
            )
            return True
        finally:
            if progress_message is not None:
                try:
                    await progress_message.delete()
                except Exception:
                    logger.info(
                        "minutes_progress_delete_failed message=%s progress_message=%s",
                        message_id,
                        progress_message.id,
                    )

    @staticmethod
    def _write_pcm_wav(path: Path, pcm: bytes, sample_rate: int = 48000, channels: int = 2, sample_width: int = 2) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)

    async def _play_wav_in_voice_channel(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        wav_path: Path,
    ) -> str | None:
        logger.info(
            "minutes_playback_prepare guild=%s channel=%s path=%s exists=%s size=%s",
            guild.id,
            voice_channel.id,
            wav_path,
            wav_path.exists(),
            wav_path.stat().st_size if wav_path.exists() else 0,
        )
        existing_vc = guild.voice_client
        if existing_vc and existing_vc.is_connected():
            current = getattr(existing_vc, "channel", None)
            if current != voice_channel:
                return "Bot が別VCに接続中です。"
            vc = existing_vc
        else:
            vc = await voice_channel.connect(self_deaf=True, reconnect=False)

        if vc.is_playing():
            return "Bot は現在再生中です。"

        source = discord.FFmpegPCMAudio(str(wav_path))

        def _after_playback(error: Exception | None) -> None:
            logger.info("minutes_playback_finished guild=%s error=%r", guild.id, error)
            if error:
                return
            fut = vc.disconnect(force=True)
            if hasattr(fut, "__await__"):
                asyncio.run_coroutine_threadsafe(fut, self.bot.loop)

        vc.play(source, after=_after_playback)
        logger.info("minutes_playback_started guild=%s channel=%s path=%s", guild.id, voice_channel.id, wav_path)
        return None

    def _build_help_text(self) -> str:
        lines: list[str] = ["Kenny Bot 使い方"]
        for section in HELP_SECTIONS:
            lines.append(f"[{section.title}]")
            lines.extend(section.lines)

        commands_by_category: dict[str, list[str]] = {category: [] for category in COMMAND_CATEGORY_ORDER}
        for meta in SLASH_COMMANDS.values():
            commands_by_category.setdefault(meta.category, []).append(
                f"/{meta.name}: {meta.description}"
            )
        for category in COMMAND_CATEGORY_ORDER:
            lines_for_category = commands_by_category.get(category, [])
            if lines_for_category:
                lines.append(f"[コマンド: {category}]")
                lines.extend(lines_for_category)
        return "\n".join(lines)

    def _build_ping_text(self) -> str:
        latency_ms = round(float(self.bot.latency) * 1000, 1)
        return f"Pong! {latency_ms}ms"

    @staticmethod
    def _message_export_author(message: discord.Message) -> str:
        author = getattr(message, "author", None)
        name = (
            getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or "unknown"
        )
        author_id = int(getattr(author, "id", 0) or 0)
        return f"{name} ({author_id})"

    @staticmethod
    def _message_export_body(message: discord.Message) -> str:
        parts: list[str] = []
        content = (getattr(message, "content", "") or "").strip()
        if content:
            parts.append(content)
        for attachment in getattr(message, "attachments", []) or []:
            url = str(getattr(attachment, "url", "") or "").strip()
            if url:
                parts.append(f"[attachment] {url}")
        body = "\n".join(parts).strip()
        return body or "[本文なし]"

    def _format_message_export_line(self, message: discord.Message) -> str:
        created_at = getattr(message, "created_at", None)
        if created_at is not None:
            time_label = created_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        else:
            time_label = "日時不明"
        body = self._message_export_body(message).replace("\r\n", "\n").replace("\r", "\n")
        body = body.replace("\n", "\n    ")
        return f"{time_label} | {self._message_export_author(message)}: {body}"

    @staticmethod
    def _append_export_line(
        lines: list[str],
        line: str,
        *,
        current_bytes: int,
        max_bytes: int,
    ) -> tuple[int, bool]:
        added_bytes = len((line + "\n\n").encode("utf-8"))
        if current_bytes + added_bytes > max_bytes:
            return current_bytes, False
        lines.append(line)
        lines.append("")
        return current_bytes + added_bytes, True

    async def _iter_export_threads(self, channel: ReadableChannel):
        if isinstance(channel, discord.Thread):
            return

        seen_thread_ids: set[int] = set()
        for thread in getattr(channel, "threads", []) or []:
            thread_id = int(getattr(thread, "id", 0) or 0)
            if thread_id and thread_id not in seen_thread_ids:
                seen_thread_ids.add(thread_id)
                yield thread

        archived_threads = getattr(channel, "archived_threads", None)
        if archived_threads is None:
            return

        for private in (False, True):
            try:
                async for thread in archived_threads(limit=None, private=private):
                    thread_id = int(getattr(thread, "id", 0) or 0)
                    if thread_id and thread_id not in seen_thread_ids:
                        seen_thread_ids.add(thread_id)
                        yield thread
            except (discord.Forbidden, discord.NotFound):
                continue

    async def _build_channel_export_text(
        self,
        channel: ReadableChannel,
        *,
        limit: int | None = None,
        max_bytes: int = 23_000_000,
    ) -> tuple[str, int, bool]:
        channel_name = getattr(channel, "name", "unknown")
        channel_id = int(getattr(channel, "id", 0) or 0)
        generated_at = now_jst().strftime("%Y-%m-%d %H:%M:%S JST")
        lines = [
            "# Kenny Bot channel message export",
            f"channel: #{channel_name} ({channel_id})",
            f"generated_at: {generated_at}",
            "",
            "[channel messages]",
            "",
        ]
        current_bytes = len(("\n".join(lines) + "\n").encode("utf-8"))
        count = 0
        truncated = False
        async for message in channel.history(limit=limit, oldest_first=True):
            line = self._format_message_export_line(message)
            current_bytes, appended = self._append_export_line(
                lines,
                line,
                current_bytes=current_bytes,
                max_bytes=max_bytes,
            )
            if not appended:
                truncated = True
                break
            count += 1
        if not truncated:
            async for thread in self._iter_export_threads(channel):
                thread_name = getattr(thread, "name", "unknown")
                thread_id = int(getattr(thread, "id", 0) or 0)
                header = f"[thread messages] #{thread_name} ({thread_id})"
                current_bytes, appended = self._append_export_line(
                    lines,
                    header,
                    current_bytes=current_bytes,
                    max_bytes=max_bytes,
                )
                if not appended:
                    truncated = True
                    break
                try:
                    async for message in thread.history(limit=limit, oldest_first=True):
                        line = self._format_message_export_line(message)
                        current_bytes, appended = self._append_export_line(
                            lines,
                            line,
                            current_bytes=current_bytes,
                            max_bytes=max_bytes,
                        )
                        if not appended:
                            truncated = True
                            break
                        count += 1
                except (discord.Forbidden, discord.NotFound):
                    current_bytes, _ = self._append_export_line(
                        lines,
                        "[このスレッドの履歴を取得する権限がありません]",
                        current_bytes=current_bytes,
                        max_bytes=max_bytes,
                    )
                if truncated:
                    break
        if truncated:
            lines.append("[以降はファイルサイズ上限のため省略]")
        return "\n".join(lines).rstrip() + "\n", count, truncated

    def _build_bot_info_embed(self) -> discord.Embed:
        now = discord.utils.utcnow()
        uptime = now - self._started_at
        total_seconds = int(max(0, uptime.total_seconds()))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60

        guild_count = len(self.bot.guilds)
        member_count = 0
        for g in self.bot.guilds:
            if g.member_count:
                member_count += int(g.member_count)

        ping_ms = round(self.bot.latency * 1000, 1)
        commit = self._git_short_commit()
        version = self._git_version()
        ai_model = self._display_model_name(get_app_config().ai_models().default)

        embed = discord.Embed(
            title="Kenny Bot 情報",
            color=discord.Color.green(),
            timestamp=now_jst(),
        )
        embed.add_field(name="疎通", value="🏓 Pong / 正常", inline=True)
        embed.add_field(name="Ping", value=f"{ping_ms} ms", inline=True)
        embed.add_field(name="稼働時間", value=f"{h}h {m}m {s}s", inline=True)
        embed.add_field(name="参加サーバー", value=str(guild_count), inline=True)
        embed.add_field(name="総メンバー数(概算)", value=str(member_count), inline=True)
        embed.add_field(name="会話モデル", value=f"`{ai_model}`", inline=True)
        embed.add_field(name="Version", value=f"`{version}`", inline=True)
        embed.add_field(name="Commit", value=f"`{commit}`", inline=True)
        return embed

    @staticmethod
    def _split_text(text: str, limit: int = 1900) -> list[str]:
        body = (text or "").strip()
        if not body:
            return []
        chunks: list[str] = []
        while body:
            if len(body) <= limit:
                chunks.append(body)
                break
            split_at = body.rfind("\n", 0, limit)
            if split_at < max(200, limit // 2):
                split_at = body.rfind(" ", 0, limit)
            if split_at < max(200, limit // 2):
                split_at = limit
            chunks.append(body[:split_at].rstrip())
            body = body[split_at:].lstrip()
        return chunks

    async def _send_help_text(self, destination, *, ephemeral: bool = False) -> None:
        text = self._build_help_text()
        chunks = self._split_text(text)
        if not chunks:
            return
        send_kwargs = {"ephemeral": ephemeral}
        await destination.send(chunks[0], **send_kwargs)
        for chunk in chunks[1:]:
            await destination.send(chunk, **send_kwargs)

    @app_commands.command(name=HELP_META.name, description=HELP_META.description)
    async def slash_help(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        interaction_message = getattr(interaction, "message", None)
        try:
            await self._send_help_text(interaction.followup, ephemeral=True)
            log_system_event(
                "help 実行",
                msg=interaction_message,
                level="info",
                details={
                    "command": "help",
                    "user_id": getattr(interaction.user, "id", 0),
                    "channel_id": interaction.channel_id,
                    "status": "ok",
                },
            )
        except Exception as e:
            log_system_event(
                "help 実行失敗",
                msg=interaction_message,
                level="error",
                details={
                    "command": "help",
                    "user_id": getattr(interaction.user, "id", 0),
                    "channel_id": interaction.channel_id,
                    "status": "error",
                    "error": str(e)[:1000],
                },
            )
            raise

    @commands.command(name=HELP_META.name)
    async def prefix_help(self, ctx: commands.Context):
        await self._send_help_text(ctx)

    @app_commands.command(name=PING_META.name, description=PING_META.description)
    async def slash_ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(self._build_ping_text(), ephemeral=True)

    def _git_short_commit(self) -> str:
        build_info = load_build_info()
        build_commit = build_info.get("commit")
        if build_commit:
            return build_commit
        try:
            cp = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            out = (cp.stdout or "").strip()
            return out or "unknown"
        except Exception:
            return "unknown"

    def _git_version(self) -> str:
        build_info = load_build_info()
        build_version = build_info.get("version")
        if build_version:
            return build_version
        commit = self._git_short_commit()
        try:
            cp = subprocess.run(
                ["git", "status", "--porcelain"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            dirty = bool((cp.stdout or "").strip())
            return f"{commit}-dirty" if dirty else commit
        except Exception:
            return commit

    def _display_model_name(self, model: str) -> str:
        value = (model or "").strip()
        if not value:
            return "unknown"
        if value.endswith("-cloud"):
            value = value[: -len("-cloud")]
        return value.replace(":", "").replace("-", "-")

    @app_commands.command(name=VC_CONTROL_META.name, description=VC_CONTROL_META.description)
    @app_commands.checks.cooldown(1, 15.0)
    async def vc_control(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        actor = interaction.user
        if not actor.guild_permissions.move_members:
            await interaction.response.send_message(
                "この操作には『通話メンバーの移動』権限が必要です。",
                ephemeral=True,
            )
            return

        voice = actor.voice
        if not voice or not isinstance(voice.channel, discord.VoiceChannel):
            await interaction.response.send_message("VCに参加してから実行してください。", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("テキストチャンネルで実行してください。", ephemeral=True)
            return

        panel_text = (
            f"🎛️ **VCコントロールパネル**（対象VC: {voice.channel.name}）\n"
            f"{self.VC_JOIN_EMOJI} 参加登録\n"
            f"{self.VC_MUTE_ON_EMOJI} ミュートON / {self.VC_MUTE_OFF_EMOJI} ミュートOFF\n"
            f"{self.VC_DEAF_ON_EMOJI} スピーカーミュートON / {self.VC_DEAF_OFF_EMOJI} スピーカーミュートOFF\n"
            "※ 参加登録済み かつ VC参加中の人だけ操作できます。"
        )
        panel_msg = await interaction.channel.send(panel_text)
        for e in (
            self.VC_JOIN_EMOJI,
            self.VC_MUTE_ON_EMOJI,
            self.VC_MUTE_OFF_EMOJI,
            self.VC_DEAF_ON_EMOJI,
            self.VC_DEAF_OFF_EMOJI,
        ):
            try:
                await panel_msg.add_reaction(e)
            except Exception:
                pass

        self._vc_panels[panel_msg.id] = VcPanelState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            voice_channel_id=voice.channel.id,
            host_user_id=interaction.user.id,
            joined_user_ids=set(),
        )
        await interaction.response.send_message("VCコントロールパネルを作成しました。", ephemeral=True)

    def _build_group_match_content(
        self,
        state: GroupMatchState,
        participants: list[discord.Member],
    ) -> str:
        title = state.title.strip() if state.title else ""
        header = f"{self.GROUP_MATCH_EMOJI} **{state.group_size}人組 自動マッチング**"
        if title:
            header += f" | {title}"

        lines = [
            header,
            f"{self.GROUP_MATCH_EMOJI} を押すと参加します。",
            f"<@{state.host_user_id}> が {self.GROUP_MATCH_START_EMOJI} を押すとシャッフルして確定します。",
            "結果表示: チャンネル公開" if state.visibility == "public" else "結果表示: 参加者へのDM送信",
            f"現在の参加者: {len(participants)}人",
            "",
        ]

        if not participants:
            lines.append("まだ参加者はいません。")
            return "\n".join(lines)
        lines.append("参加者:")
        lines.extend(f"- {member.mention}" for member in participants)

        return "\n".join(lines)

    def _build_group_match_result_content(
        self,
        state: GroupMatchState,
        participants: list[discord.Member],
    ) -> str:
        title = state.title.strip() if state.title else ""
        header = f"{self.GROUP_MATCH_EMOJI} **{state.group_size}人組 シャッフル結果**"
        if title:
            header += f" | {title}"

        lines = [
            header,
            f"参加者: {len(participants)}人",
            "",
        ]
        if not participants:
            lines.append("参加者がいないため、組み分けできませんでした。")
            return "\n".join(lines)

        groups = [
            participants[index:index + state.group_size]
            for index in range(0, len(participants), state.group_size)
        ]
        for idx, group in enumerate(groups, start=1):
            mentions = " / ".join(member.mention for member in group)
            label = f"{idx}組目"
            if len(group) < state.group_size:
                label += " (端数)"
            lines.append(f"{label}: {mentions}")
        return "\n".join(lines)

    async def _collect_group_match_participants(
        self,
        message: discord.Message,
        guild: discord.Guild,
    ) -> list[discord.Member]:
        users: list[discord.Member] = []
        for reaction in message.reactions:
            if str(reaction.emoji) not in reaction_aliases(self.GROUP_MATCH_EMOJI):
                continue
            async for user in reaction.users():
                if not isinstance(user, discord.Member):
                    member = guild.get_member(user.id)
                else:
                    member = user
                if not isinstance(member, discord.Member) or member.bot:
                    continue
                if member not in users:
                    users.append(member)
            break
        users.sort(key=lambda member: member.display_name.lower())
        return users

    async def _refresh_group_match(self, message_id: int) -> None:
        state = self._group_matches.get(message_id)
        if not state:
            return

        guild = self.bot.get_guild(state.guild_id)
        if not guild:
            self._group_matches.pop(message_id, None)
            return
        channel = self.bot.get_channel(state.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            self._group_matches.pop(message_id, None)
            return
        except Exception:
            return

        participants = await self._collect_group_match_participants(message, guild)
        content = self._build_group_match_content(state, participants)
        if message.content != content:
            await message.edit(content=content)

    @app_commands.command(name=GROUP_MATCH_META.name, description=GROUP_MATCH_META.description)
    @app_commands.checks.cooldown(1, 10.0)
    @app_commands.describe(
        size="作る組の人数",
        title="募集タイトル（任意）",
        visibility="結果の公開範囲",
    )
    @app_commands.choices(size=_GROUP_SIZE_CHOICES, visibility=_GROUP_VISIBILITY_CHOICES)
    async def group_match(
        self,
        interaction: discord.Interaction,
        size: app_commands.Choice[int],
        visibility: app_commands.Choice[str] | None = None,
        title: str | None = None,
    ):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("サーバーのテキストチャンネルで実行してください。", ephemeral=True)
            return

        state = GroupMatchState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            host_user_id=interaction.user.id,
            group_size=int(size.value),
            visibility=visibility.value if visibility is not None else "public",
            title=title.strip() if title else None,
        )
        content = self._build_group_match_content(state, [])
        message = await interaction.channel.send(content)
        try:
            await message.add_reaction(self.GROUP_MATCH_EMOJI)
        except Exception:
            pass
        try:
            await message.add_reaction(self.GROUP_MATCH_START_EMOJI)
        except Exception:
            pass

        self._group_matches[message.id] = state
        await interaction.response.send_message(
            f"{int(size.value)}人組の募集メッセージを作成しました。",
            ephemeral=True,
        )

    @app_commands.command(name=VRCHAT_WORLD_META.name, description=VRCHAT_WORLD_META.description)
    @app_commands.checks.cooldown(1, 10.0)
    @app_commands.describe(
        keyword="検索キーワード",
        count="取得件数",
        author="作者名で部分一致フィルタ",
        tag="タグで絞り込み",
        totp_code="TOTP 2FA コード（管理者のみ、必要時だけ）",
        email_code="Email 2FA コード（管理者のみ、必要時だけ）",
    )
    async def vrchat_world(
        self,
        interaction: discord.Interaction,
        keyword: str,
        count: app_commands.Range[int, 1, 10] | None = None,
        author: str | None = None,
        tag: str | None = None,
        totp_code: str | None = None,
        email_code: str | None = None,
    ):
        search_keyword = keyword.strip()
        if not search_keyword:
            await interaction.response.send_message("検索キーワードを指定してください。", ephemeral=True)
            return
        has_2fa_code = bool((totp_code or "").strip() or (email_code or "").strip())
        if has_2fa_code and not self._is_admin_like(interaction):
            await interaction.response.send_message(
                "2FA コードを使った VRChat 認証は管理者のみ実行できます。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=has_2fa_code)

        try:
            formatter, worlds = await asyncio.to_thread(
                search_vrchat_worlds,
                search_keyword,
                int(count or 5),
                author.strip() if author else None,
                tag.strip() if tag else None,
                totp_code=totp_code,
                email_code=email_code,
            )
        except VRChatTwoFactorRequired as exc:
            logger.info("VRChat world search requires 2FA")
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except VRChatAuthError as exc:
            logger.warning("VRChat auth setup failed: %s", exc)
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as e:
            logger.exception("VRChat world search failed")
            await interaction.followup.send(
                f"VRChat ワールド検索に失敗しました: {sanitize_user_visible_error(e, max_chars=300)}",
                ephemeral=True,
            )
            return

        if not worlds:
            await interaction.followup.send("該当するワールドが見つかりませんでした。")
            return

        lines = format_vrchat_world_lines(formatter, worlds)
        message = "\n".join(lines)
        if has_2fa_code:
            message = "VRChat 認証 cookie を保存しました。\n\n" + message
        if len(message) > 1900:
            message = message[:1900] + "\n..."
        await interaction.followup.send(message, ephemeral=has_2fa_code)

    @app_commands.command(name=VRC_USER_META.name, description=VRC_USER_META.description)
    @app_commands.checks.cooldown(1, 10.0)
    @app_commands.describe(
        url="VRChat ユーザーURLまたは usr_ から始まるユーザーID",
        totp_code="TOTP 2FA コード（管理者のみ、必要時だけ）",
        email_code="Email 2FA コード（管理者のみ、必要時だけ）",
    )
    async def vrc_user(
        self,
        interaction: discord.Interaction,
        url: str,
        totp_code: str | None = None,
        email_code: str | None = None,
    ):
        has_2fa_code = bool((totp_code or "").strip() or (email_code or "").strip())
        if has_2fa_code and not self._is_admin_like(interaction):
            await interaction.response.send_message(
                "2FA コードを使った VRChat 認証は管理者のみ実行できます。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=has_2fa_code)
        try:
            lookup = await asyncio.to_thread(
                get_vrchat_user_from_url,
                url.strip(),
                totp_code=totp_code,
                email_code=email_code,
            )
        except VRChatTwoFactorRequired as exc:
            logger.info("VRChat user lookup requires 2FA")
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except VRChatAuthError as exc:
            logger.warning("VRChat auth setup failed: %s", exc)
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            logger.exception("VRChat user lookup failed")
            await interaction.followup.send(
                f"VRChat ユーザー取得に失敗しました: {sanitize_user_visible_error(exc, max_chars=300)}",
                ephemeral=True,
            )
            return

        message = format_vrchat_user(lookup.user)
        if lookup.cookie_saved and has_2fa_code:
            message = "VRChat 認証 cookie を保存しました。\n\n" + message
        if len(message) > 1900:
            message = message[:1900] + "\n..."
        await interaction.followup.send(message, ephemeral=has_2fa_code)

    @app_commands.command(name=BOT_INFO_META.name, description=BOT_INFO_META.description)
    async def slash_bot_info(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)
        await interaction.followup.send(embed=self._build_bot_info_embed(), ephemeral=True)

    @commands.command(name=BOT_INFO_META.name)
    async def prefix_bot_info(self, ctx: commands.Context):
        await ctx.send(embed=self._build_bot_info_embed())

    @app_commands.command(name=SUMMARIZE_RECENT_META.name, description=SUMMARIZE_RECENT_META.description)
    @app_commands.checks.cooldown(1, 20.0)
    @app_commands.describe(
        messages="何件を要約するか（1〜設定上限、省略時は設定値）",
        request="要約の仕方の要望（例: 一言で / 箇条書きで / 詳細めに）",
    )
    async def summarize_recent(
        self,
        interaction: discord.Interaction,
        messages: app_commands.Range[int, 1, 300] | None = None,
        request: str | None = None,
    ):
        target = interaction.channel
        if not self._is_readable_channel(target):
            await interaction.response.send_message("このチャンネルでは要約できません。", ephemeral=True)
            return

        guild_id = interaction.guild.id if interaction.guild else 0
        default_recent = int(
            _settings.get(
                "summarize_recent_default_messages",
                _settings.get("summarize_recent_default_minutes", 30, guild_id=guild_id),
                guild_id=guild_id,
            )
        )
        max_messages = int(
            _settings.get(
                "summarize_recent.max_messages",
                _settings.get("summarize_recent.max_minutes", 300, guild_id=guild_id),
                guild_id=guild_id,
            )
        )
        fetch_limit = int(_settings.get("summarize_recent.history_fetch_limit", 300, guild_id=guild_id))
        line_limit = int(_settings.get("summarize_recent.transcript_lines_limit", 120, guild_id=guild_id))
        messages_val = int(messages) if messages is not None else default_recent
        if messages_val < 1:
            messages_val = 1
        if max_messages > 0 and messages_val > max_messages:
            messages_val = max_messages

        await interaction.response.defer(ephemeral=True, thinking=True)

        rows: List[str] = []
        matched_count = 0
        history_limit = max(100, fetch_limit, messages_val * 2)
        async for m in target.history(limit=history_limit):
            if m.author.bot:
                continue
            text = (m.content or "").strip()
            if not text:
                continue
            name = m.author.display_name if isinstance(m.author, discord.Member) else m.author.name
            time_label = m.created_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")
            rows.append(f"[{time_label}] {name} ({m.author.id}): {text[:400]}")
            matched_count += 1
            if matched_count >= messages_val:
                break

        if not rows:
            await interaction.followup.send(
                f"要約対象メッセージが見つかりませんでした。（指定: {messages_val}件）",
                ephemeral=True,
            )
            return

        rows = list(reversed(rows[:messages_val]))
        prompt_line_limit = max(20, line_limit)
        prompt_rows = rows[-prompt_line_limit:]
        transcript = "\n".join(prompt_rows)
        request_text = (request or "").strip()
        if len(request_text) > 300:
            request_text = request_text[:300]
        prompt = get_prompt("slash", "summarize_recent_prompt").format(
            channel_name=target.name,
            row_count=len(rows),
            prompt_row_count=len(prompt_rows),
            user_request=request_text or "指定なし",
            transcript=transcript,
        )
        progress_key = f"ai-progress:{interaction.channel_id}:summarize:{interaction.user.id}"
        model_summary = get_app_config().ai_models().summary
        ticket = await self.bot.ai_progress_tracker.create_ticket()

        try:
            await self._countdowns.start_countup(
                key=progress_key,
                channel=interaction.channel,
                mention_user_id=interaction.user.id,
                text_factory=lambda elapsed, model=model_summary: self.bot.ai_progress_tracker.render(
                    ticket, elapsed, model
                ),
            )
            await self.bot.ai_progress_tracker.acquire(ticket)
            try:
                summary = self.bot.ollama_client.chat_simple(
                    model=model_summary,
                    prompt=prompt,
                    stream=False,
                )
            finally:
                await self.bot.ai_progress_tracker.release(ticket)
            summary = (summary or "").strip() or "要約結果が空でした。"
        except Exception as e:
            await interaction.followup.send(
                f"要約に失敗しました: {sanitize_user_visible_error(e, max_chars=180)}",
                ephemeral=True,
            )
            return
        finally:
            await self._countdowns.stop(progress_key, delete_message=True)

        if len(summary) > 1800:
            summary = summary[:1800] + "\n...(省略)..."

        embed = discord.Embed(
            title=f"直近{len(rows)}件のチャット要約",
            description=summary,
            color=discord.Color.orange(),
            timestamp=now_jst(),
        )
        footer_bits = [f"#{target.name}", "対象: このチャンネル", f"件数: {len(rows)}"]
        if request_text:
            footer_bits.append(f"要望: {request_text[:30]}")
        embed.set_footer(text=" / ".join(footer_bits))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name=EXPORT_CHANNEL_MESSAGES_META.name, description=EXPORT_CHANNEL_MESSAGES_META.description)
    @app_commands.checks.cooldown(1, 60.0)
    @app_commands.describe(
        channel="取得するチャンネル（省略時はこのチャンネル）",
        limit="最大取得件数（省略時は取得可能な全履歴）",
    )
    async def export_channel_messages(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
        limit: app_commands.Range[int, 1, 100000] | None = None,
    ):
        if not await self._ensure_admin_like(interaction):
            return
        target = channel or interaction.channel
        if not self._is_readable_channel(target):
            await interaction.response.send_message("このチャンネルの履歴は取得できません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            text, count, truncated = await self._build_channel_export_text(
                target,
                limit=int(limit) if limit is not None else None,
            )
        except (discord.Forbidden, discord.NotFound):
            await interaction.followup.send("チャンネル履歴を取得する権限がありません。", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"チャンネル履歴の取得に失敗しました: {sanitize_user_visible_error(e, max_chars=180)}",
                ephemeral=True,
            )
            return

        channel_name = re.sub(r"[^0-9A-Za-z_.-]+", "-", str(getattr(target, "name", "channel"))).strip("-")
        if not channel_name:
            channel_name = "channel"
        filename = f"{channel_name}-messages-{now_jst().strftime('%Y%m%d-%H%M%S')}.txt"
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename=filename)
        suffix = "（サイズ上限で一部省略）" if truncated else ""
        await interaction.followup.send(
            content=f"#{getattr(target, 'name', 'channel')} の履歴を {count} 件出力しました。{suffix}",
            file=file,
            ephemeral=True,
        )

    @app_commands.command(name=SET_RECENT_WINDOW_META.name, description=SET_RECENT_WINDOW_META.description)
    @app_commands.describe(messages="既定の件数（1〜300）")
    async def set_recent_window(
        self,
        interaction: discord.Interaction,
        messages: app_commands.Range[int, 1, 300],
    ):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        _settings.set("summarize_recent_default_messages", int(messages), guild_id=interaction.guild.id)
        await interaction.response.send_message(
            f"要約の既定件数を **{int(messages)}件** に設定しました。\n"
            "次回 `/summarize_recent` で messages 省略時に適用されます。",
            ephemeral=True,
        )

    @app_commands.command(name=CONFIG_META.name, description=CONFIG_META.description)
    @app_commands.describe(
        action="操作",
        key="設定キー",
        value="新しい値（set のときだけ使用）",
        scope="global:全体 / guild:このサーバーのみ",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="表示", value="show"),
            app_commands.Choice(name="更新", value="set"),
        ],
        scope=[
            app_commands.Choice(name="global", value="global"),
            app_commands.Choice(name="guild", value="guild"),
        ],
    )
    async def config(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        key: str,
        value: str | None = None,
        scope: app_commands.Choice[str] | None = None,
    ):
        if not await self._ensure_admin_like(interaction):
            return
        key_name = key.strip()
        valid_keys = {choice.value for choice in self._CONFIG_CHOICES}
        if key_name not in valid_keys:
            await interaction.response.send_message("未対応の設定キーです。", ephemeral=True)
            return
        action_name = (action.value or "").strip().lower()
        gid = interaction.guild.id if interaction.guild else None
        if action_name == "show":
            value_now = _settings.get(key_name, None, guild_id=gid)
            await interaction.response.send_message(
                f"`{key_name}` = `{value_now}`",
                ephemeral=True,
            )
            return

        if action_name != "set":
            await interaction.response.send_message("action は `show` / `set` から選んでください。", ephemeral=True)
            return

        if value is None:
            await interaction.response.send_message("更新には `value` が必要です。", ephemeral=True)
            return

        sc = scope.value if scope else "global"
        guild_id = interaction.guild.id if (sc == "guild" and interaction.guild) else None
        if sc == "guild" and guild_id is None:
            await interaction.response.send_message("guild スコープはサーバー内で実行してください。", ephemeral=True)
            return
        if sc == "global":
            if not interaction.guild or (
                interaction.user.id != interaction.guild.owner_id and not self._is_configured_admin_user(interaction.user.id)
            ):
                await interaction.response.send_message(
                    "global スコープはサーバーオーナーまたは設定済み管理者のみ変更できます。",
                    ephemeral=True,
                )
                return

        parsed: object
        if key_name in self._INT_KEYS:
            try:
                parsed = int(value)
            except Exception:
                await interaction.response.send_message("このキーは整数で指定してください。", ephemeral=True)
                return
        elif key_name in self._BOOL_KEYS:
            v = value.strip().lower()
            if v in {"1", "true", "on", "yes", "有効"}:
                parsed = True
            elif v in {"0", "false", "off", "no", "無効"}:
                parsed = False
            else:
                await interaction.response.send_message("このキーは true/false で指定してください。", ephemeral=True)
                return
        elif key_name in self._INT_LIST_KEYS:
            parts = re.split(r"[\s,]+", value.strip()) if value.strip() else []
            parsed_ids: list[int] = []
            for part in parts:
                try:
                    parsed_ids.append(int(part))
                except Exception:
                    await interaction.response.send_message(
                        "このキーはユーザーIDをカンマまたは空白区切りで指定してください。",
                        ephemeral=True,
                    )
                    return
            if not parsed_ids:
                await interaction.response.send_message("少なくとも1件のユーザーIDを指定してください。", ephemeral=True)
                return
            parsed = parsed_ids
        else:
            parsed = value.strip()
            if not parsed:
                await interaction.response.send_message("空文字は設定できません。", ephemeral=True)
                return

        _settings.set(key_name, parsed, guild_id=guild_id)
        note = "（一部設定は再起動後に完全反映）"
        await interaction.response.send_message(
            f"設定を更新しました: `{key_name}` = `{parsed}` / scope=`{sc}` {note}",
            ephemeral=True,
        )

    @config.autocomplete("key")
    async def config_key_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._autocomplete_config_keys(current)

    def _ollama_model_key(self, target: str) -> str:
        mapping = {
            "default": "ai.models.default",
            "chat": "ai.models.chat",
            "summary": "ai.models.summary",
            "embedding": "ai.models.embedding",
        }
        return mapping[target]

    def _is_local_host(self, host: str | None) -> bool:
        if not host:
            return True
        parsed = urlparse(host if "://" in host else f"http://{host}")
        hostname = (parsed.hostname or "").lower()
        return hostname in {"localhost", "127.0.0.1", "::1", "ollama"}

    def _normalize_model_name_for_target(self, target: str, model_name: str) -> str:
        normalized = (model_name or "").strip()
        if normalized.lower().startswith("gemini"):
            return normalized
        remote_host = os.getenv("OLLAMA_HOST")
        if target != "embedding" and remote_host and not self._is_local_host(remote_host):
            if normalized and not normalized.endswith("-cloud"):
                normalized += "-cloud"
        return normalized

    def _list_gemini_models(self) -> list[str]:
        if not ((os.getenv("GEMINI_API_KEY") or "").strip() or (os.getenv("GOOGLE_API_KEY") or "").strip()):
            return []
        return [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ]

    def _list_models_for_host(self, host: str | None) -> list[str]:
        client = create_ollama_client(host=host)
        return client.list_model_names()

    def _list_local_models_via_cli(self) -> list[str]:
        cp = subprocess.run(
            ["ollama", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or "").strip() or "unknown error"
            raise RuntimeError(err[:300])

        names: list[str] = []
        for idx, line in enumerate((cp.stdout or "").splitlines()):
            row = line.strip()
            if not row:
                continue
            if idx == 0 and row.lower().startswith("name"):
                continue
            parts = row.split()
            if parts:
                names.append(parts[0])
        return names

    def _list_local_models(self) -> list[str]:
        try:
            names = self._list_local_models_via_cli()
        except Exception:
            names = self._list_remote_models_via_tags_api("http://127.0.0.1:11434")
        return names

    def _list_remote_models_via_tags_api(self, host: str) -> list[str]:
        base = (host or "").rstrip("/")
        if base == "https://ollama.com":
            url = "https://ollama.com/api/tags"
        else:
            url = f"{base}/api/tags"

        cp = subprocess.run(
            ["curl", "-sS", url],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or "").strip() or "unknown error"
            raise RuntimeError(err[:300])

        try:
            payload = json.loads(cp.stdout or "{}")
        except Exception as e:
            raise RuntimeError(f"invalid response: {e}") from e

        models = payload.get("models", [])
        names: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name if name.endswith("-cloud") else f"{name}-cloud")
        return sorted(set(names))

    @app_commands.command(name=MODEL_LIST_META.name, description=MODEL_LIST_META.description)
    async def model_list(
        self,
        interaction: discord.Interaction,
    ):
        if not await self._ensure_admin_like(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        local_host = "http://127.0.0.1:11434"
        remote_host = os.getenv("OLLAMA_HOST")
        sections: list[str] = []

        try:
            local_names = await asyncio.to_thread(self._list_local_models)
            local_body = "\n".join(f"- `{name}`" for name in local_names[:30]) if local_names else "0件"
        except Exception as e:
            local_body = f"取得失敗: `{sanitize_user_visible_error(e, max_chars=200)}`"
        sections.append(f"ローカル:\n{local_body}")

        if remote_host and remote_host != local_host:
            try:
                remote_names = await asyncio.to_thread(self._list_remote_models_via_tags_api, remote_host)
                remote_body = "\n".join(f"- `{name}`" for name in remote_names[:30]) if remote_names else "0件"
            except Exception as e:
                remote_body = f"取得失敗: `{sanitize_user_visible_error(e, max_chars=200)}`"
            sections.append(f"リモート ({remote_host}):\n{remote_body}")

        await interaction.followup.send("\n\n".join(sections), ephemeral=True)

    @app_commands.command(name=MODEL_CHANGE_META.name, description=MODEL_CHANGE_META.description)
    @app_commands.describe(
        target="切り替える用途",
        model="設定するモデル名",
    )
    @app_commands.choices(target=_OLLAMA_MODEL_TARGET_CHOICES)
    async def model_change(
        self,
        interaction: discord.Interaction,
        target: app_commands.Choice[str],
        model: str,
    ):
        if not await self._ensure_admin_like(interaction):
            return
        raw_model_name = model.strip()
        model_name = self._normalize_model_name_for_target(target.value, raw_model_name)
        if not model_name:
            await interaction.response.send_message("モデル名を指定してください。", ephemeral=True)
            return

        key = self._ollama_model_key(target.value)
        local_names: list[str] = []
        remote_names: list[str] = []
        try:
            local_names = await asyncio.to_thread(self._list_local_models)
        except Exception:
            local_names = []
        remote_host = os.getenv("OLLAMA_HOST")
        if remote_host and not self._is_local_host(remote_host):
            try:
                remote_names = await asyncio.to_thread(self._list_remote_models_via_tags_api, remote_host)
            except Exception:
                remote_names = []

        available_names = set(local_names) | set(remote_names)
        if available_names and model_name not in available_names:
            await interaction.response.send_message(
                f"`{model_name}` は現在のモデル一覧に見つかりませんでした。\n"
                "先に `/model_list` で確認してください。",
                ephemeral=True,
            )
            return

        _settings.set(key, model_name)
        if target.value == "default":
            self.bot.ollama_model = model_name
        await interaction.response.send_message(
            f"`{key}` を `{model_name}` に設定しました。",
            ephemeral=True,
        )

    @app_commands.command(name=REACTION_ROLE_SET_META.name, description=REACTION_ROLE_SET_META.description)
    @app_commands.describe(
        message_id="対象メッセージID",
        emoji="対象リアクション",
        role="付与するロール",
    )
    async def reaction_role_set(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        role: discord.Role,
    ):
        if not await self._ensure_admin_like(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        try:
            msg_id = str(int(message_id.strip()))
        except Exception:
            await interaction.response.send_message("message_id は数値で指定してください。", ephemeral=True)
            return

        emoji_key = emoji.strip()
        if not emoji_key:
            await interaction.response.send_message("emoji を指定してください。", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("Botに『ロールの管理』権限がありません。", ephemeral=True)
            return
        if role >= me.top_role:
            await interaction.response.send_message(
                "そのロールはBotの最上位ロール以上なので付与できません。",
                ephemeral=True,
            )
            return

        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=interaction.guild.id)
        if not isinstance(bindings, dict):
            bindings = {}
        per_message = bindings.get(msg_id, {})
        if not isinstance(per_message, dict):
            per_message = {}
        per_message[emoji_key] = int(role.id)
        bindings[msg_id] = per_message
        _settings.set("reaction_roles.bindings", bindings, guild_id=interaction.guild.id)
        add_reaction_note = ""
        try:
            target_message = await self._find_message_for_reaction_role(
                interaction.guild,
                int(msg_id),
                preferred_channel=interaction.channel,
            )
            if target_message is None:
                add_reaction_note = "\n対象メッセージは見つからなかったため、Bot のリアクション追加はスキップしました。"
            else:
                await target_message.add_reaction(emoji_key)
                add_reaction_note = "\n対象メッセージに Bot がリアクションを追加しました。"
        except discord.HTTPException:
            add_reaction_note = "\n設定は保存しましたが、Bot が対象メッセージへリアクションを追加できませんでした。"
        await interaction.response.send_message(
            f"登録しました: message_id=`{msg_id}` / emoji=`{emoji_key}` / role={role.mention}\n"
            "このリアクションを押したユーザーにロールを付与します。"
            f"{add_reaction_note}",
            ephemeral=True,
        )

    @app_commands.command(name=REACTION_ROLE_REMOVE_META.name, description=REACTION_ROLE_REMOVE_META.description)
    @app_commands.describe(
        message_id="対象メッセージID",
        emoji="解除するリアクション",
    )
    async def reaction_role_remove(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
    ):
        if not await self._ensure_admin_like(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        try:
            msg_id = str(int(message_id.strip()))
        except Exception:
            await interaction.response.send_message("message_id は数値で指定してください。", ephemeral=True)
            return

        emoji_key = emoji.strip()
        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=interaction.guild.id)
        if not isinstance(bindings, dict):
            bindings = {}
        per_message = bindings.get(msg_id, {})
        if not isinstance(per_message, dict) or emoji_key not in per_message:
            await interaction.response.send_message("対象設定が見つかりません。", ephemeral=True)
            return

        per_message.pop(emoji_key, None)
        if per_message:
            bindings[msg_id] = per_message
        else:
            bindings.pop(msg_id, None)
        _settings.set("reaction_roles.bindings", bindings, guild_id=interaction.guild.id)
        await interaction.response.send_message(
            f"解除しました: message_id=`{msg_id}` / emoji=`{emoji_key}`",
            ephemeral=True,
        )

    @app_commands.command(name=REACTION_ROLE_LIST_META.name, description=REACTION_ROLE_LIST_META.description)
    async def reaction_role_list(self, interaction: discord.Interaction):
        if not await self._ensure_admin_like(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=interaction.guild.id)
        if not isinstance(bindings, dict) or not bindings:
            await interaction.response.send_message("リアクションロール設定はありません。", ephemeral=True)
            return

        lines: List[str] = []
        for msg_id, per_message in bindings.items():
            if not isinstance(per_message, dict):
                continue
            for emoji_key, role_id in per_message.items():
                role = interaction.guild.get_role(int(role_id))
                role_text = role.mention if role else f"`{role_id}`"
                lines.append(f"message_id=`{msg_id}` / emoji=`{emoji_key}` / role={role_text}")

        if not lines:
            await interaction.response.send_message("リアクションロール設定はありません。", ephemeral=True)
            return

        await interaction.response.send_message("\n".join(lines[:30]), ephemeral=True)

    @app_commands.command(name=MOD_PANEL_META.name, description=MOD_PANEL_META.description)
    async def modpanel(self, interaction: discord.Interaction):
        if not await self._ensure_admin_like(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        channel = self.bot.get_channel(MOD_PANEL_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("モデレーションパネルチャンネルが見つかりません。", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛡️ スパム管理パネル",
            description=(
                "このパネルを使用してスパムユーザーを管理できます。\n\n"
                "**使い方：**\n"
                "1. ユーザーIDを記載したメッセージにリアクションを追加\n"
                f"2. {get_reaction_emoji('mod_reset')} を押すとリセット\n"
                f"3. {get_reaction_emoji('mod_list')} を押すと違反一覧表示\n\n"
                "**例:**\n"
                "`ユーザーID: 123456789`\n"
                "`レベル: mute`\n"
                "`違反回数: 3`"
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="mod_panel")

        msg = await channel.send(embed=embed)
        await msg.add_reaction(get_reaction_emoji("mod_reset"))
        await msg.add_reaction(get_reaction_emoji("mod_list"))
        await interaction.response.send_message("✅ モデレーションパネルを作成しました。", ephemeral=True)

    @app_commands.command(name=MINUTES_META.name, description=MINUTES_META.description)
    @app_commands.describe(
        action="操作",
        model="文字起こしモデル。Whisper 系または Moonshine を選択",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="開始", value="start"),
            app_commands.Choice(name="停止", value="stop"),
            app_commands.Choice(name="状態", value="status"),
        ],
        model=_WHISPER_MODEL_CHOICES,
    )
    async def minutes(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        model: app_commands.Choice[str] | None = None,
    ):
        action_name = (action.value or "").strip().lower()
        if action_name == "stop":
            await self.minutes_stop(interaction)
            return
        if action_name == "status":
            await self.minutes_status(interaction)
            return

        logger.info(
            "minutes_start_requested guild=%s channel=%s user=%s model=%s",
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id,
            model.value if model else "default",
        )
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        voice = interaction.user.voice
        if not voice or not isinstance(voice.channel, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.response.send_message("VCに参加してから実行してください。", ephemeral=True)
            return
        voice_channel = voice.channel
        voice_channel_name = voice_channel.name

        existing_vc = interaction.guild.voice_client
        switched_from_tts = False
        if existing_vc and existing_vc.is_connected():
            current = getattr(existing_vc, "channel", None)
            current_name = current.name if isinstance(current, (discord.VoiceChannel, discord.StageChannel)) else "不明"
            tts_reader = self.bot.get_cog("TTSReader")
            tts_state = tts_reader._get_state(interaction.guild.id) if tts_reader is not None else None
            same_channel = getattr(current, "id", None) == voice_channel.id
            if same_channel and tts_state is not None and hasattr(tts_reader, "stop_for_minutes"):
                await interaction.response.defer(ephemeral=True, thinking=True)
                try:
                    await tts_reader.stop_for_minutes(interaction.guild)
                    switched_from_tts = True
                    logger.info(
                        "minutes_start_stopped_tts guild=%s voice_channel=%s",
                        interaction.guild.id,
                        voice_channel.id,
                    )
                except Exception as e:
                    logger.exception("minutes_start failed to stop TTS")
                    await interaction.followup.send(
                        f"読み上げ停止に失敗したため議事録を開始できません: "
                        f"{sanitize_user_visible_error(e, max_chars=180)}",
                        ephemeral=True,
                    )
                    return
            else:
                await interaction.response.send_message(
                    f"Bot はすでに VC `{current_name}` に接続中です。"
                    " 別用途の接続を停止してから再試行してください。",
                    ephemeral=True,
                )
                return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        selected_model = model.value if model else None
        provider = None
        backend_model = None
        if selected_model:
            if selected_model.startswith("moonshine/"):
                provider = "moonshine"
                backend_model = selected_model
            elif selected_model.startswith("whisper/"):
                provider = "whisper"
                backend_model = selected_model.split("/", 1)[1]
            else:
                provider = "whisper"
                backend_model = selected_model

        ok, msg = await self.bot.meeting_minutes.start_session(
            bot=self.bot,
            guild=interaction.guild,
            voice_channel=voice_channel,
            started_by_id=interaction.user.id,
            announce_channel_id=interaction.channel_id,
            transcription_provider=provider,
            whisper_model=backend_model,
        )
        if switched_from_tts:
            msg += "\n読み上げを停止して議事録モードへ切り替えました。"
        logger.info(
            "minutes_start_result guild=%s user=%s ok=%s message=%s",
            interaction.guild.id,
            interaction.user.id,
            ok,
            msg.replace("\n", " / ")[:500],
        )
        await interaction.followup.send(msg, ephemeral=True)
        if ok:
            out = self.bot.meeting_minutes.resolve_announce_channel(
                self.bot,
                interaction.guild,
                interaction.channel_id,
                allow_fallback=False,
            )
            if out:
                session = self.bot.meeting_minutes.get_session(interaction.guild.id)
                runtime = getattr(session, "runtime", None)
                supports_realtime = getattr(runtime, "voice_client", None) is not None
                mode_line = "リアル文字起こし: 開始中" if supports_realtime else "録音方式: 外部レコーダー（停止時に要約）"
                start_message = await out.send(
                    "\n".join(
                        [
                            f"{interaction.user.mention} 議事録を開始しました。（VC: {voice_channel_name}）",
                            "リアル文字起こしの投稿は行いません。",
                            f"{self.MINUTES_SUMMARY_EMOJI} 途中要約",
                            f"{self.MINUTES_STOP_EMOJI} 停止して要約",
                            f"{self.MINUTES_PLAYBACK_EMOJI} 停止して録音を再生",
                        ]
                    )
                )
                for emoji in (self.MINUTES_SUMMARY_EMOJI, self.MINUTES_STOP_EMOJI, self.MINUTES_PLAYBACK_EMOJI):
                    try:
                        await start_message.add_reaction(emoji)
                    except Exception:
                        pass
                self._minutes_panels[start_message.id] = MinutesControlState(
                    guild_id=interaction.guild.id,
                    channel_id=out.id,
                    message_id=start_message.id,
                    status_message_id=0,
                    owner_user_id=interaction.user.id,
                    voice_channel_id=voice_channel.id,
                )
                try:
                    status_message = await out.send(
                        self._build_minutes_status_text(
                            interaction.guild,
                            self.bot.meeting_minutes.get_session(interaction.guild.id),
                        )
                    )
                    self._minutes_panels[start_message.id].status_message_id = status_message.id
                except Exception:
                    pass
            log_ch = self.bot.meeting_minutes.resolve_global_log_channel(self.bot)
            if log_ch and out != log_ch:
                await log_ch.send(
                    f"[minutes_start] guild={interaction.guild.id} channel={interaction.channel_id} "
                    f"user={interaction.user.id} vc={voice_channel_name} provider={provider or 'default'} model={backend_model or 'default'}"
                )

    async def _toggle_minutes_realtime_mode(
        self,
        guild: discord.Guild,
        member: discord.Member,
        panel: MinutesControlState,
    ) -> str:
        session = self.bot.meeting_minutes.get_session(guild.id)
        if session is None:
            return "進行中の議事録がありません。"
        runtime = session.runtime
        if runtime.voice_client is None:
            return "リアル文字起こしはこの録音方式では使えません。"
        if runtime.realtime_live_enabled:
            task = runtime.realtime_task
            runtime.realtime_live_enabled = False
            runtime.realtime_task = None
            for flush_task in list(getattr(runtime, "phrase_flush_tasks", {}).values()):
                flush_task.cancel()
            getattr(runtime, "phrase_flush_tasks", {}).clear()
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            await self._update_minutes_status_message(guild, panel)
            return f"{member.mention} リアル文字起こし投稿を停止しました。"

        if runtime.phrase_queue is None:
            runtime.phrase_queue = asyncio.Queue()
        if runtime.realtime_task is None or runtime.realtime_task.done():
            runtime.realtime_task = asyncio.create_task(self.bot.meeting_minutes._run_realtime_updates(self.bot, guild.id))
        runtime.realtime_live_enabled = True
        await self._update_minutes_status_message(guild, panel)
        return f"{member.mention} リアル文字起こし投稿を開始しました。"

    def _build_minutes_status_text(self, guild: discord.Guild, session: object | None, ended: bool = False) -> str:
        if ended or session is None:
            return "\n".join(
                [
                    "音声認識ステータス: 終了",
                    "要約ステータス: 終了",
                    "音声認識と文字起こしは停止しました。",
                ]
            )
        voice_channel_id = getattr(session, "voice_channel_id", 0)
        voice_channel = guild.get_channel(voice_channel_id)
        voice_channel_name = voice_channel.name if isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)) else f"ID:{voice_channel_id}"
        runtime = getattr(session, "runtime", None)
        voice_client = getattr(runtime, "voice_client", None)
        recorder_process = getattr(runtime, "recorder_process", None)
        received_packets = int(getattr(runtime, "received_packets", 0) or 0)
        received_pcm_bytes = int(getattr(runtime, "received_pcm_bytes", 0) or 0)
        if recorder_process is not None:
            recognition_state = "録音中"
        elif voice_client is None:
            recognition_state = "停止中"
        elif received_packets > 0:
            recognition_state = "音声受信中"
        else:
            recognition_state = "接続済み・音声待機中"
        return "\n".join(
            [
                f"音声認識ステータス: {recognition_state}",
                "要約ステータス: 停止時に実行",
                f"受信音声: {received_packets} packets / {received_pcm_bytes / (1024 * 1024):.2f} MiB",
                f"対象VC: {voice_channel_name}",
            ]
        )

    async def _update_minutes_status_message(
        self,
        guild: discord.Guild,
        panel: MinutesControlState,
        *,
        ended: bool = False,
    ) -> None:
        channel = self.bot.get_channel(panel.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            msg = await channel.fetch_message(panel.status_message_id)
        except Exception:
            return
        session = self.bot.meeting_minutes.get_session(guild.id)
        try:
            await msg.edit(content=self._build_minutes_status_text(guild, session, ended=ended))
        except Exception:
            pass

    async def _send_minutes_interim_summary(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel | discord.Thread,
    ) -> str:
        ok, payload = await self.bot.meeting_minutes.build_interim_summary(
            self.bot,
            guild,
            reason="途中要約",
        )
        if not ok:
            return str(payload)
        if isinstance(payload, discord.Embed):
            await channel.send(embed=payload)
            return "途中要約を送信しました。"
        return str(payload)

    @staticmethod
    async def _ensure_minutes_voice_disconnected(guild: discord.Guild) -> str:
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            return " / BotはVCから退出しました"
        try:
            if vc.is_playing():
                vc.stop()
            await vc.disconnect(force=True)
            return " / BotはVCから退出しました"
        except Exception as e:
            logger.exception("Failed to disconnect minutes voice client")
            return f" / VC退出失敗: {sanitize_user_visible_error(e, max_chars=120)}"

    async def _stop_minutes_session_from_panel(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel | discord.Thread,
        member: discord.Member,
        *,
        playback: bool,
        action: str,
    ) -> str:
        logger.info(
            "minutes_stop_panel_start guild=%s user=%s playback=%s",
            guild.id,
            member.id,
            playback,
        )
        if playback:
            stopped = await self.bot.meeting_minutes.stop_session_for_playback(guild)
            logger.info("minutes_stop_playback_recording_result guild=%s found=%s", guild.id, stopped is not None)
            if stopped is None:
                return "現在、進行中の議事録はありません。"
            session, audio_path, warning = stopped
            voice_channel = guild.get_channel(session.voice_channel_id)
            playback_note = ""
            if audio_path is None:
                playback_note = f"録音再生なし: {warning or '再生できる録音がありません。'}"
            elif not isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
                playback_note = "録音再生なし: 対象VCが見つかりません。"
            else:
                try:
                    playback_error = await self._play_wav_in_voice_channel(guild, voice_channel, audio_path)
                    playback_note = f"録音再生なし: {playback_error}" if playback_error else "録音MP3をVCで再生中"
                except Exception as e:
                    playback_note = f"録音再生失敗: {sanitize_user_visible_error(e, max_chars=180)}"
            await self._set_minutes_status_ended(guild)
            self._clear_minutes_panels(guild.id)
            return f"文字起こし・要約を破棄して議事録を停止しました。{playback_note}"

        result = await self.bot.meeting_minutes.stop_session(
            bot=self.bot,
            guild=guild,
            reason=f"{member.display_name} がリアクションで停止",
            mention_user_id=member.id,
        )
        if not result:
            return "現在、進行中の議事録はありません。"

        playback_note = await self._ensure_minutes_voice_disconnected(guild)

        await self.bot.meeting_minutes.deliver_stop_result(
            self.bot,
            guild,
            result,
            action=action,
            source_channel_id=channel.id,
            playback_note=playback_note,
        )
        await self._set_minutes_status_ended(guild)
        self._clear_minutes_panels(guild.id)
        return "議事録を停止し、要約を作成しました。" + playback_note

    async def minutes_stop(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, text = await self._stop_minutes_session(
            interaction,
            playback=False,
            action="minutes_stop",
        )
        await interaction.followup.send(text if ok else text, ephemeral=True)

    async def minutes_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        session = self.bot.meeting_minutes.get_session(interaction.guild.id)
        if not session:
            await interaction.response.send_message("議事録は停止中です。", ephemeral=True)
            return

        vc = interaction.guild.get_channel(session.voice_channel_id)
        started = session.started_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        vc_name = vc.name if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)) else f"ID:{session.voice_channel_id}"
        await interaction.response.send_message(
            f"議事録は進行中です。\nVC: {vc_name}\n開始: {started}\n文字起こし: {session.transcription_provider or '設定値'}\nmodel: {session.whisper_model or '設定値'}",
            ephemeral=True,
        )

    async def _stop_minutes_session(
        self,
        interaction: discord.Interaction,
        *,
        playback: bool,
        action: str,
    ) -> tuple[bool, str]:
        if not interaction.guild:
            return False, "サーバー内で実行してください。"
        result = await self.bot.meeting_minutes.stop_session(
            bot=self.bot,
            guild=interaction.guild,
            reason=f"{interaction.user.display_name} が手動停止",
            mention_user_id=interaction.user.id,
        )
        if not result:
            return False, "現在、進行中の議事録はありません。"

        playback_note = ""
        if playback:
            voice_channel = interaction.guild.get_channel(result.session.voice_channel_id)
            wav_path = None
            if result.audio_debug_paths:
                for candidate in result.audio_debug_paths:
                    path = Path(candidate)
                    if path.suffix.lower() == ".wav":
                        wav_path = path
                        break
                if wav_path is None:
                    wav_path = Path(result.audio_debug_paths[0])
            if wav_path and wav_path.exists() and isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
                try:
                    playback_error = await self._play_wav_in_voice_channel(interaction.guild, voice_channel, wav_path)
                    if playback_error:
                        playback_note = f" / 録音再生なし: {playback_error}"
                    else:
                        playback_note = " / 録音をVCで再生中"
                except Exception as e:
                    playback_note = f" / 録音再生失敗: {e}"
        else:
            playback_note = await self._ensure_minutes_voice_disconnected(interaction.guild)

        await self.bot.meeting_minutes.deliver_stop_result(
            self.bot,
            interaction.guild,
            result,
            action=action,
            source_channel_id=interaction.channel_id,
            playback_note=playback_note,
        )
        await self._set_minutes_status_ended(interaction.guild)
        self._clear_minutes_panels(interaction.guild.id)
        return True, "議事録を停止し、要約を作成しました。" + playback_note

    @app_commands.command(name=TIMER_META.name, description=TIMER_META.description)
    @app_commands.checks.cooldown(2, 10.0)
    @app_commands.describe(
        hours="時間（0〜23）",
        minutes="分（0〜59）",
        seconds="秒（0〜59）",
        title="終了時に表示するメッセージ（任意）",
    )
    async def timer(
        self,
        interaction: discord.Interaction,
        hours: app_commands.Range[int, 0, 23] = 0,
        minutes: app_commands.Range[int, 0, 59] = 0,
        seconds: app_commands.Range[int, 0, 59] = 0,
        title: str | None = None,
    ):
        total_seconds = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if total_seconds <= 0:
            await interaction.response.send_message(
                "時間を指定してください（例: 0時間 1分 30秒）。",
                ephemeral=True,
            )
            return
        if total_seconds > 24 * 3600:
            await interaction.response.send_message(
                "最大24時間までにしてください。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"⏱️ タイマー開始: {hours}時間 {minutes}分 {seconds}秒",
            ephemeral=True,
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await self._run_timer_countdown(
                channel=interaction.channel,
                mention_user_id=interaction.user.id,
                total_seconds=total_seconds,
                title=title,
            )
            return

        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=total_seconds))
        done_text = title.strip() if title and title.strip() else "タイマー終了です。"
        try:
            await interaction.user.send(f"⏰ {done_text}")
        except Exception:
            pass

    async def _run_timer_countdown(
        self,
        channel: discord.TextChannel,
        mention_user_id: int,
        total_seconds: int,
        title: str | None,
    ) -> None:
        done_text = title.strip() if title and title.strip() else "タイマー終了です。"

        async def _after_done(countdown_msg: discord.Message) -> None:
            try:
                await countdown_msg.edit(
                    content=f"<@{mention_user_id}> ⏰ {done_text}\n{self.TIMER_RESTART_EMOJI} を押すと同じ設定で再スタート"
                )
            except Exception:
                return
            try:
                await countdown_msg.add_reaction(self.TIMER_RESTART_EMOJI)
            except Exception:
                pass
            self._timer_restart_templates[countdown_msg.id] = (int(total_seconds), done_text)

        await self._countdowns.start_or_replace(
            key=f"timer:{channel.id}:{mention_user_id}",
            channel=channel,
            initial_text=f"⏳ 残り {total_seconds} 秒",
            total_seconds=total_seconds,
            mention_user_id=mention_user_id,
            done_text=f"⏰ {done_text}",
            on_done=_after_done,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        emoji = self._emoji_key(payload.emoji)
        logger.info(
            "raw_reaction_add message=%s channel=%s guild=%s user=%s emoji=%s",
            payload.message_id,
            payload.channel_id,
            payload.guild_id,
            payload.user_id,
            emoji,
        )
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        channel = self.bot.get_channel(payload.channel_id)
        await self._handle_minutes_reaction(
            guild=guild,
            channel=channel,
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            guild_id=payload.guild_id,
            user_id=payload.user_id,
            emoji=emoji,
        )

        if payload.message_id in self._group_matches:
            state = self._group_matches.get(payload.message_id)
            if not state:
                return
            if emoji in reaction_aliases(self.GROUP_MATCH_EMOJI):
                await self._refresh_group_match(payload.message_id)
                return
            if emoji in reaction_aliases(self.GROUP_MATCH_START_EMOJI) and payload.user_id == state.host_user_id:
                guild = self.bot.get_guild(state.guild_id)
                channel = self.bot.get_channel(state.channel_id)
                if not guild or not isinstance(channel, discord.TextChannel):
                    return
                try:
                    message = await channel.fetch_message(payload.message_id)
                except Exception:
                    return
                participants = await self._collect_group_match_participants(message, guild)
                shuffled = list(participants)
                random.shuffle(shuffled)
                if state.visibility == "private":
                    result_text = self._build_group_match_result_content(state, shuffled)
                    failures: list[str] = []
                    for member in participants:
                        try:
                            await member.send(result_text)
                        except Exception:
                            failures.append(member.mention)
                    notice = f"{self.GROUP_MATCH_EMOJI} 組み分け結果を参加者へDMしました。"
                    if failures:
                        notice += "\nDM失敗: " + ", ".join(failures[:5])
                    await message.edit(content=notice)
                else:
                    await message.edit(content=self._build_group_match_result_content(state, shuffled))
                self._group_matches.pop(payload.message_id, None)
                return

        # タイマー再スタート
        if emoji in reaction_aliases(self.TIMER_RESTART_EMOJI):
            tpl = self._timer_restart_templates.get(payload.message_id)
            if not tpl:
                return
            channel = self.bot.get_channel(payload.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            seconds, done_text = tpl
            asyncio.create_task(
                self._run_timer_countdown(
                    channel=channel,
                    mention_user_id=payload.user_id,
                    total_seconds=int(seconds),
                    title=done_text,
                )
            )
            return

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.id == (self.bot.user.id if self.bot.user else 0):
            return
        message = reaction.message
        emoji = self._emoji_key(reaction.emoji)
        logger.info(
            "reaction_add message=%s channel=%s guild=%s user=%s emoji=%s",
            message.id,
            message.channel.id,
            getattr(message.guild, "id", None),
            user.id,
            emoji,
        )
        # Raw reaction events are the canonical path. Handling both events runs
        # the same minutes action twice for messages present in Discord's cache.
        logger.debug("reaction_add_skipped_raw_handler_is_canonical message=%s", message.id)
        return

        panel = self._vc_panels.get(payload.message_id)
        if not panel:
            return
        if emoji not in {
            self.VC_JOIN_EMOJI,
            self.VC_MUTE_ON_EMOJI,
            self.VC_MUTE_OFF_EMOJI,
            self.VC_DEAF_ON_EMOJI,
            self.VC_DEAF_OFF_EMOJI,
        }:
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild or guild.id != panel.guild_id:
            return
        member = guild.get_member(payload.user_id)
        if not isinstance(member, discord.Member):
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        # 参加登録
        if emoji in reaction_aliases(self.VC_JOIN_EMOJI):
            if not member.voice or not isinstance(member.voice.channel, discord.VoiceChannel):
                await channel.send(f"{member.mention} VC参加中のみ登録できます。", delete_after=5)
                return
            if member.voice.channel.id != panel.voice_channel_id:
                await channel.send(f"{member.mention} 対象VCに参加してから登録してください。", delete_after=5)
                return
            panel.joined_user_ids.add(member.id)
            await channel.send(f"{member.mention} を参加登録しました。", delete_after=5)
            return

        # 操作側の条件: 参加登録済み + 対象VCに接続中 + move_members 権限
        if member.id not in panel.joined_user_ids:
            await channel.send(f"{member.mention} 先に {self.VC_JOIN_EMOJI} で参加登録してください。", delete_after=5)
            return
        if not member.voice or not isinstance(member.voice.channel, discord.VoiceChannel) or member.voice.channel.id != panel.voice_channel_id:
            await channel.send(f"{member.mention} 対象VCに参加中のときのみ操作できます。", delete_after=5)
            return
        if not member.guild_permissions.move_members:
            await channel.send(f"{member.mention} この操作には『通話メンバーの移動』権限が必要です。", delete_after=5)
            return

        me = guild.me
        if me is None:
            return
        targets = []
        vc = guild.get_channel(panel.voice_channel_id)
        if isinstance(vc, discord.VoiceChannel):
            for uid in panel.joined_user_ids:
                tm = guild.get_member(uid)
                if isinstance(tm, discord.Member) and tm.voice and tm.voice.channel and tm.voice.channel.id == vc.id and not tm.bot:
                    targets.append(tm)

        op = None
        if emoji in reaction_aliases(self.VC_MUTE_ON_EMOJI):
            op = "mute_on"
            if not me.guild_permissions.mute_members:
                await channel.send("Botに『メンバーをミュート』権限がありません。", delete_after=5)
                return
        elif emoji in reaction_aliases(self.VC_MUTE_OFF_EMOJI):
            op = "mute_off"
            if not me.guild_permissions.mute_members:
                await channel.send("Botに『メンバーをミュート』権限がありません。", delete_after=5)
                return
        elif emoji in reaction_aliases(self.VC_DEAF_ON_EMOJI):
            op = "deafen_on"
            if not me.guild_permissions.deafen_members:
                await channel.send("Botに『メンバーをスピーカーミュート』権限がありません。", delete_after=5)
                return
        elif emoji in reaction_aliases(self.VC_DEAF_OFF_EMOJI):
            op = "deafen_off"
            if not me.guild_permissions.deafen_members:
                await channel.send("Botに『メンバーをスピーカーミュート』権限がありません。", delete_after=5)
                return
        if op is None:
            return

        success = 0
        failed = 0
        for tm in targets:
            if tm.id == me.id or tm.top_role >= me.top_role:
                failed += 1
                continue
            try:
                if op == "mute_on":
                    await tm.edit(mute=True, reason=f"{member} によるVCパネル操作")
                elif op == "mute_off":
                    await tm.edit(mute=False, reason=f"{member} によるVCパネル操作")
                elif op == "deafen_on":
                    await tm.edit(deafen=True, reason=f"{member} によるVCパネル操作")
                elif op == "deafen_off":
                    await tm.edit(deafen=False, reason=f"{member} によるVCパネル操作")
                success += 1
            except Exception:
                failed += 1

        await channel.send(
            f"{member.mention} 操作を実行しました。成功 {success} / 失敗 {failed}",
            delete_after=7,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        if str(payload.emoji) not in reaction_aliases(self.GROUP_MATCH_EMOJI):
            return
        if payload.message_id not in self._group_matches:
            return
        await self._refresh_group_match(payload.message_id)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            text = f"連続実行を制限中です。{error.retry_after:.1f}秒後に再試行してください。"
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return
        logger.exception("Slash command failed", exc_info=error)
        safe_error = sanitize_user_visible_error(error, max_chars=1000)
        interaction_message = getattr(interaction, "message", None)
        log_codex_repair_mode(
            msg=interaction_message,
            trigger="slash_command_error",
            issue=str(error),
            planned_fix="スラッシュコマンド例外と管理ログの記録経路を再確認する",
            target_area=interaction.command.qualified_name if interaction.command else "unknown",
            level="error",
        )
        log_system_event(
            "スラッシュコマンド失敗",
            msg=interaction_message,
            level="error",
            details={
                "command": interaction.command.qualified_name if interaction.command else "unknown",
                "user_id": getattr(interaction.user, "id", 0),
                "channel_id": interaction.channel_id,
                "error": safe_error,
                "handled_by": "cog_app_command_error",
            },
        )
        await send_event_log(
            self.bot,
            guild=interaction.guild,
            level="error",
            title="スラッシュコマンド失敗",
            description="スラッシュコマンドの実行に失敗しました。",
            fields=[
                ("コマンド", interaction.command.qualified_name if interaction.command else "unknown", True),
                ("ユーザー", f"{interaction.user} ({interaction.user.id})", False),
                ("チャンネル", str(interaction.channel_id), True),
                ("エラー", safe_error, False),
            ],
            source_channel_id=interaction.channel_id,
        )
        text = "コマンド実行に失敗しました。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            logger.exception("Failed to send slash command error response")
