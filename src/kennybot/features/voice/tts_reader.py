# cogs/tts_reader.py
# VOICEVOX を使ったテキスト読み上げ

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.utils.command_catalog import get_slash_command_meta
from src.kennybot.utils.runtime_settings import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()
ReadableChannel = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread
TTS_JOIN_META = get_slash_command_meta("tts_join")
TTS_LEAVE_META = get_slash_command_meta("tts_leave")
TTS_VOICE_META = get_slash_command_meta("tts_voice")
TTS_STATUS_META = get_slash_command_meta("tts_status")


@dataclass
class GuildTtsState:
    channel_id: int
    speaker_id: int
    queue: list[str] = field(default_factory=list)
    playing: bool = False


class TTSReader(commands.Cog):
    """VOICEVOX 読み上げ"""

    _SPEAKER_CHOICES = [
        app_commands.Choice(name="ずんだもん ノーマル", value=3),
        app_commands.Choice(name="ずんだもん あまあま", value=1),
        app_commands.Choice(name="ずんだもん ツンツン", value=7),
        app_commands.Choice(name="四国めたん ノーマル", value=2),
        app_commands.Choice(name="四国めたん あまあま", value=0),
        app_commands.Choice(name="春日部つむぎ ノーマル", value=8),
        app_commands.Choice(name="波音リツ ノーマル", value=9),
        app_commands.Choice(name="雨晴はう ノーマル", value=10),
        app_commands.Choice(name="玄野武宏 ノーマル", value=11),
        app_commands.Choice(name="WhiteCUL ノーマル", value=23),
    ]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, GuildTtsState] = {}

    def _get_state(self, guild_id: int) -> GuildTtsState | None:
        return self._states.get(guild_id)

    def _is_supported_channel(self, channel: object) -> bool:
        return isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread))

    def _cleanup_text(self, text: str, max_chars: int) -> str:
        text = " ".join((text or "").split())
        if not text:
            return ""
        if len(text) > max_chars:
            text = text[:max_chars] + " 省略"
        return text

    def _voicevox_url(self) -> str:
        return str(
            os.getenv("VOICEVOX_URL")
            or _settings.get("tts.voicevox_url", "http://127.0.0.1:50021")
        ).rstrip("/")

    def _speaker_id(self, guild_id: int) -> int:
        return int(_settings.get("tts.speaker_id", 3, guild_id=guild_id))

    def _max_chars(self, guild_id: int) -> int:
        return int(_settings.get("tts.max_chars", 120, guild_id=guild_id))

    def _speaker_label(self, speaker_id: int) -> str:
        for choice in self._SPEAKER_CHOICES:
            if int(choice.value) == int(speaker_id):
                return choice.name
        return f"speaker_id={speaker_id}"

    def _synthesize_to_file(self, text: str, speaker_id: int) -> str:
        base_url = self._voicevox_url()
        encoded = urllib.parse.urlencode({"text": text, "speaker": speaker_id})
        query_req = urllib.request.Request(
            f"{base_url}/audio_query?{encoded}",
            method="POST",
        )
        try:
            with urllib.request.urlopen(query_req, timeout=10) as res:
                query = res.read()
            synth_req = urllib.request.Request(
                f"{base_url}/synthesis?speaker={speaker_id}",
                data=query,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(synth_req, timeout=20) as res:
                audio = res.read()
        except urllib.error.URLError as e:
            raise RuntimeError(f"VOICEVOX に接続できません: {e}") from e

        fd, path = tempfile.mkstemp(prefix="kennybot_tts_", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
        except Exception:
            try:
                os.unlink(path)
            except Exception:
                pass
            raise
        return path

    async def _play_next(self, guild_id: int) -> None:
        state = self._states.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        if state is None or guild is None:
            return
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            self._states.pop(guild_id, None)
            return
        if vc.is_playing() or state.playing or not state.queue:
            return

        state.playing = True
        text = state.queue.pop(0)
        tmp_path = ""
        try:
            tmp_path = await asyncio.to_thread(self._synthesize_to_file, text, state.speaker_id)
            source = discord.FFmpegPCMAudio(tmp_path)

            def _after_playback(error: Exception | None) -> None:
                try:
                    if tmp_path:
                        Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Failed to remove temp TTS file")
                state.playing = False
                if error:
                    logger.exception("TTS playback failed: %s", error)
                asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.bot.loop)

            vc.play(source, after=_after_playback)
        except Exception:
            state.playing = False
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
            logger.exception("Failed to synthesize or play TTS audio")

    @app_commands.command(name=TTS_JOIN_META.name, description=TTS_JOIN_META.description)
    @app_commands.describe(speaker="読み上げ話者")
    @app_commands.choices(speaker=_SPEAKER_CHOICES)
    async def tts_join(self, interaction: discord.Interaction, speaker: app_commands.Choice[int] | None = None):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if not self._is_supported_channel(interaction.channel):
            await interaction.response.send_message("このチャンネルでは読み上げを開始できません。", ephemeral=True)
            return
        target_channel = interaction.channel

        voice = interaction.user.voice
        if not voice or not isinstance(voice.channel, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.response.send_message("VCに参加してから実行してください。", ephemeral=True)
            return
        voice_channel = voice.channel

        await interaction.response.defer(ephemeral=True, thinking=True)

        me = interaction.guild.me
        if me is None:
            await interaction.followup.send("Botメンバー情報を取得できません。", ephemeral=True)
            return
        perms = voice_channel.permissions_for(me)
        if not perms.connect or not perms.speak:
            await interaction.followup.send("BotにVC接続または発話権限がありません。", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            if vc.channel and vc.channel.id != voice_channel.id:
                await vc.move_to(voice_channel)
        else:
            try:
                await voice_channel.connect(self_deaf=True, reconnect=False)
            except Exception as e:
                await interaction.followup.send(f"VC接続に失敗しました: {e}", ephemeral=True)
                return

        state = GuildTtsState(
            channel_id=target_channel.id,
            speaker_id=int(speaker.value if speaker is not None else self._speaker_id(interaction.guild.id)),
        )
        self._states[interaction.guild.id] = state
        await interaction.followup.send(
            f"読み上げを開始しました。対象チャンネル: {target_channel.mention} / "
            f"対象VC: `{voice_channel.name}` / 話者: `{self._speaker_label(state.speaker_id)}`",
            ephemeral=True,
        )

    @app_commands.command(name=TTS_LEAVE_META.name, description=TTS_LEAVE_META.description)
    async def tts_leave(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        self._states.pop(interaction.guild.id, None)
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception as e:
                await interaction.response.send_message(f"VC切断に失敗しました: {e}", ephemeral=True)
                return
        await interaction.response.send_message("読み上げを停止しました。", ephemeral=True)

    @app_commands.command(name=TTS_VOICE_META.name, description=TTS_VOICE_META.description)
    @app_commands.describe(speaker="読み上げ話者")
    @app_commands.choices(speaker=_SPEAKER_CHOICES)
    async def tts_voice(self, interaction: discord.Interaction, speaker: app_commands.Choice[int]):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        speaker_id = int(speaker.value)
        _settings.set("tts.speaker_id", speaker_id, guild_id=interaction.guild.id)
        state = self._states.get(interaction.guild.id)
        if state:
            state.speaker_id = speaker_id
        await interaction.response.send_message(
            f"読み上げ話者を `{speaker.name}` に設定しました。",
            ephemeral=True,
        )

    @app_commands.command(name=TTS_STATUS_META.name, description=TTS_STATUS_META.description)
    async def tts_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        state = self._get_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("読み上げは停止中です。", ephemeral=True)
            return
        channel = interaction.guild.get_channel(state.channel_id)
        vc = interaction.guild.voice_client
        text_name = channel.mention if self._is_supported_channel(channel) else f"`{state.channel_id}`"
        vc_name = vc.channel.name if vc and vc.channel else "未接続"
        await interaction.response.send_message(
            f"読み上げ中です。\n対象チャンネル: {text_name}\nVC: `{vc_name}`\n話者: `{self._speaker_label(state.speaker_id)}`\n待機キュー: {len(state.queue)}件",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        state = self._states.get(message.guild.id)
        if state is None or message.channel.id != state.channel_id:
            return
        vc = message.guild.voice_client
        if vc is None or not vc.is_connected():
            return

        text = self._cleanup_text(message.clean_content, self._max_chars(message.guild.id))
        if not text:
            return

        spoken = f"{message.author.display_name}。 {text}"
        state.queue.append(spoken)
        await self._play_next(message.guild.id)
