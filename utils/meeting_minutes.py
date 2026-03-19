# utils/meeting_minutes.py
# 通話議事録（音声文字起こし + AI要約）

from __future__ import annotations

import asyncio
import importlib
import io
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from ai.google_speech import GoogleSpeechClient, GoogleSpeechConfig
from utils.runtime_settings import get_settings

JST = timezone(timedelta(hours=9))
_settings = get_settings()
VoiceLikeChannel = discord.VoiceChannel | discord.StageChannel
AnnounceChannel = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread


@dataclass
class _RecordingRuntime:
    voice_client: object | None = None
    sink: object | None = None
    chunks: dict[int, bytearray] = field(default_factory=dict)
    phrase_chunks: dict[int, bytearray] = field(default_factory=dict)
    phrase_queue: asyncio.Queue[tuple[int, bytes]] | None = None
    warning: str = ""
    max_total_bytes: int = 64 * 1024 * 1024
    max_user_bytes: int = 8 * 1024 * 1024
    dropped: bool = False
    realtime_task: asyncio.Task | None = None


@dataclass
class MeetingSession:
    guild_id: int
    voice_channel_id: int
    started_by_id: int
    started_at: datetime
    announce_channel_id: int | None = None
    runtime: _RecordingRuntime = field(default_factory=_RecordingRuntime)


@dataclass
class MeetingStopResult:
    session: MeetingSession
    ended_at: datetime
    reason: str
    transcript_line_count: int
    summary: str
    mention_user_id: int
    used_ai: bool
    truncated: bool
    translated: bool = False
    error: str = ""


class MeetingMinutesManager:
    """議事録セッション管理"""

    def __init__(self):
        self._sessions: dict[int, MeetingSession] = {}
        self._whisper_models: dict[str, object] = {}
        self._google_client: GoogleSpeechClient | None = None

    def get_session(self, guild_id: int) -> Optional[MeetingSession]:
        return self._sessions.get(guild_id)

    @staticmethod
    def _is_realtime_enabled(guild_id: int) -> bool:
        return bool(_settings.get("meeting.realtime_translation_enabled", True, guild_id=guild_id))

    @staticmethod
    def _realtime_min_audio_bytes(guild_id: int) -> int:
        return max(48000, int(_settings.get("meeting.realtime_translation_min_audio_bytes", 384000, guild_id=guild_id)))

    @staticmethod
    def _can_send(channel: AnnounceChannel, me: discord.Member | None) -> bool:
        if me is None:
            return True
        perms = channel.permissions_for(me)
        if isinstance(channel, discord.Thread):
            return bool(perms.send_messages_in_threads or perms.send_messages)
        return bool(perms.send_messages)

    @staticmethod
    def _fallback_summary(
        vc_name: str,
        reason: str,
        transcript_lines: list[str],
        transcript_line_count: int,
        duration_min: int,
    ) -> str:
        excerpt = transcript_lines[:8]
        preview = "\n".join(f"- {line[:180]}" for line in excerpt) if excerpt else "- 発話なし"
        return (
            "1) 会議概要\n"
            f"- {vc_name} の議事録です。録音時間は約 {duration_min} 分、発話行数は {transcript_line_count} 件でした。\n"
            f"- 停止理由: {reason}\n\n"
            "2) 文字起こし抜粋\n"
            f"{preview}\n\n"
            "3) 次アクション/未解決事項\n"
            "- AI要約に失敗したため、必要に応じて抜粋ログから確認してください。"
        )

    @staticmethod
    def _translation_prompt(transcript: str, target_language: str) -> str:
        return (
            "以下は通話の文字起こしログです。\n"
            f"内容を失わないように自然な {target_language} に翻訳してください。\n"
            "話者ラベル(user:123)は可能な限り維持してください。\n"
            "説明や要約は不要で、翻訳済みログだけを返してください。\n\n"
            f"{transcript}"
        )

    def _get_whisper_model(self, guild_id: int):
        try:
            fw = importlib.import_module("faster_whisper")
            WhisperModel = getattr(fw, "WhisperModel")
        except Exception:
            raise RuntimeError("faster-whisper 未導入のため文字起こしできません。")

        whisper_model = str(_settings.get("meeting.whisper_model", "tiny", guild_id=guild_id))
        model = self._whisper_models.get(whisper_model)
        if model is None:
            model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
            self._whisper_models[whisper_model] = model
        return model

    def _get_google_client(self, guild_id: int) -> GoogleSpeechClient:
        cfg = GoogleSpeechConfig(
            language_code=str(_settings.get("meeting.google_language_code", "ja-JP", guild_id=guild_id)),
            chunk_seconds=max(5, int(_settings.get("meeting.google_chunk_seconds", 20, guild_id=guild_id))),
            timeout_sec=max(10, int(_settings.get("meeting.google_timeout_sec", 90, guild_id=guild_id))),
            model=str(_settings.get("meeting.google_model", "", guild_id=guild_id)).strip(),
        )
        if (
            self._google_client is None
            or self._google_client.config.language_code != cfg.language_code
            or self._google_client.config.chunk_seconds != cfg.chunk_seconds
            or self._google_client.config.timeout_sec != cfg.timeout_sec
            or self._google_client.config.model != cfg.model
        ):
            self._google_client = GoogleSpeechClient(cfg)
        return self._google_client

    def _transcribe_chunk_map_with_google(self, chunk_map: dict[int, bytes], guild_id: int) -> list[str]:
        client = self._get_google_client(guild_id)
        lines: list[str] = []
        for uid, pcm in chunk_map.items():
            if not pcm:
                continue
            text = client.transcribe_pcm(pcm, sample_rate_hz=48000, channels=2)
            if text:
                lines.append(f"user:{uid} {text}")
        return lines

    def _transcribe_chunk_map(self, chunk_map: dict[int, bytes], guild_id: int) -> list[str]:
        provider = str(_settings.get("meeting.transcription_provider", "google", guild_id=guild_id)).strip().lower()
        google_error = ""

        if provider == "google":
            try:
                return self._transcribe_chunk_map_with_google(chunk_map, guild_id)
            except Exception as e:
                google_error = str(e)

        model = self._get_whisper_model(guild_id)
        lines: list[str] = []
        for uid, pcm in chunk_map.items():
            if not pcm:
                continue
            wav_bytes = self._pcm_to_wav(pcm, sample_rate=48000, channels=2, sample_width=2)
            segments, _info = model.transcribe(io.BytesIO(wav_bytes), language="ja")
            text = " ".join([seg.text.strip() for seg in segments if getattr(seg, "text", "").strip()])
            if text:
                lines.append(f"user:{uid} {text}")
        if google_error:
            if lines:
                return lines
            raise RuntimeError(f"Google Speech-to-Text に失敗し、Whisper フォールバックも空でした: {google_error}")
        return lines

    def _maybe_translate_text(self, bot: commands.Bot, guild_id: int, text: str) -> tuple[str, bool]:
        if not text.strip():
            return text, False
        target_language = str(_settings.get("meeting.translation_target_language", "ja", guild_id=guild_id) or "ja").strip() or "ja"
        try:
            model_summary = str(_settings.get("ollama.model_summary", "gpt-oss:120b", guild_id=guild_id))
            translated_text = bot.ollama_client.chat_simple(
                model=model_summary,
                prompt=self._translation_prompt(text, target_language),
                stream=False,
            )
            translated_text = (translated_text or "").strip()
            if translated_text:
                return translated_text, True
        except Exception:
            pass
        return text, False

    async def _run_realtime_updates(self, bot: commands.Bot, guild_id: int) -> None:
        while True:
            session = self._sessions.get(guild_id)
            if session is None:
                return
            if not self._is_realtime_enabled(guild_id) or session.runtime.phrase_queue is None:
                await asyncio.sleep(1)
                continue
            try:
                uid, pcm = await session.runtime.phrase_queue.get()
            except asyncio.CancelledError:
                raise

            try:
                lines = await asyncio.to_thread(self._transcribe_chunk_map, {uid: pcm}, guild_id)
            except Exception as e:
                session.runtime.warning = str(e)
                continue
            if not lines:
                continue

            guild = bot.get_guild(guild_id)
            if guild is None:
                continue
            out_ch = self.resolve_announce_channel(guild, session.announce_channel_id, allow_fallback=False)
            if out_ch is None:
                continue
            member = guild.get_member(uid)
            speaker_name = member.display_name if member else f"user:{uid}"
            text = "\n".join(lines)
            translated_text, translated = await asyncio.to_thread(self._maybe_translate_text, bot, guild_id, text)
            embed = discord.Embed(
                title="リアルタイム文字起こし",
                description=(translated_text[:3800] + "\n...(省略)...") if len(translated_text) > 3800 else translated_text,
                color=discord.Color.blue(),
                timestamp=datetime.now(JST),
            )
            embed.add_field(name="話者", value=speaker_name, inline=True)
            embed.add_field(name="翻訳", value="有効" if translated else "なし", inline=True)
            try:
                await out_ch.send(embed=embed)
            except Exception:
                continue

    async def start_session(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        voice_channel: VoiceLikeChannel,
        started_by_id: int,
        announce_channel_id: int | None = None,
    ) -> tuple[bool, str]:
        if guild.id in self._sessions:
            current = self._sessions[guild.id]
            return False, f"すでに議事録が進行中です（VC ID: {current.voice_channel_id}）。"

        runtime = await self._start_recording(bot, voice_channel)
        if runtime.voice_client is None and runtime.warning:
            return False, runtime.warning

        self._sessions[guild.id] = MeetingSession(
            guild_id=guild.id,
            voice_channel_id=voice_channel.id,
            started_by_id=started_by_id,
            started_at=discord.utils.utcnow(),
            announce_channel_id=announce_channel_id,
            runtime=runtime,
        )
        if self._is_realtime_enabled(guild.id):
            runtime.phrase_queue = asyncio.Queue()
            runtime.realtime_task = asyncio.create_task(self._run_realtime_updates(bot, guild.id))

        msg = f"議事録を開始しました。対象VC: {voice_channel.name}"
        if runtime.warning:
            msg += f"\n注意: {runtime.warning}"
        return True, msg

    async def stop_session(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        reason: str,
        mention_user_id: int | None = None,
    ) -> Optional[MeetingStopResult]:
        session = self._sessions.pop(guild.id, None)
        if not session:
            return None
        task = session.runtime.realtime_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        ended_at = discord.utils.utcnow()
        transcript_lines, warning = await self._stop_recording_and_transcribe(session.runtime)
        transcript_line_count = len(transcript_lines)
        mention_uid = mention_user_id or session.started_by_id

        if transcript_line_count == 0:
            note = "対象期間の音声文字起こしを取得できませんでした。"
            if warning:
                note += f"\n理由: {warning}"
            return MeetingStopResult(
                session=session,
                ended_at=ended_at,
                reason=reason,
                transcript_line_count=0,
                summary=note,
                mention_user_id=mention_uid,
                used_ai=False,
                truncated=False,
                error=warning or "",
            )

        transcript = "\n".join(transcript_lines)
        truncated = False
        if len(transcript) > 12000:
            head = transcript[:7000]
            tail = transcript[-3500:]
            transcript = head + "\n...(中略)...\n" + tail
            truncated = True

        vc = guild.get_channel(session.voice_channel_id)
        vc_name = vc.name if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)) else f"ID:{session.voice_channel_id}"
        duration_min = max(1, int((ended_at - session.started_at).total_seconds() // 60))
        translated = False
        summary_source = transcript
        if self._is_realtime_enabled(guild.id):
            summary_source, translated = await asyncio.to_thread(self._maybe_translate_text, bot, guild.id, transcript)

        prompt = (
            "以下は通話の文字起こしログです。日本語で議事録を作成してください。\n"
            "形式:\n"
            "1) 会議概要（3行以内）\n"
            "2) 決定事項（箇条書き）\n"
            "3) 未解決事項（箇条書き）\n"
            "4) 次アクション（担当が推定できる場合は名前付き）\n\n"
            f"会議VC: {vc_name}\n"
            f"会議時間(分): {duration_min}\n"
            f"停止理由: {reason}\n"
            f"発話行数: {transcript_line_count}\n\n"
            f"{summary_source}"
        )

        try:
            model_summary = str(_settings.get("ollama.model_summary", "gpt-oss:120b", guild_id=guild.id))
            summary = bot.ollama_client.chat_simple(
                model=model_summary,
                prompt=prompt,
                stream=False,
            )
            summary = (summary or "").strip() or "要約結果が空でした。"
            if len(summary) > 1800:
                summary = summary[:1800] + "\n...(省略)..."
            return MeetingStopResult(
                session=session,
                ended_at=ended_at,
                reason=reason,
                transcript_line_count=transcript_line_count,
                summary=summary,
                mention_user_id=mention_uid,
                used_ai=True,
                truncated=truncated,
                translated=translated,
                error=warning or "",
            )
        except Exception as e:
            return MeetingStopResult(
                session=session,
                ended_at=ended_at,
                reason=reason,
                transcript_line_count=transcript_line_count,
                summary=self._fallback_summary(vc_name, reason, transcript_lines, transcript_line_count, duration_min),
                mention_user_id=mention_uid,
                used_ai=False,
                truncated=truncated,
                translated=translated,
                error=str(e),
            )

    @staticmethod
    def is_human_empty(channel: discord.VoiceChannel) -> bool:
        humans = [m for m in channel.members if not m.bot]
        return len(humans) == 0

    @staticmethod
    def resolve_announce_channel(
        guild: discord.Guild,
        preferred_channel_id: int | None,
        *,
        allow_fallback: bool = True,
    ) -> AnnounceChannel | None:
        me = guild.me
        preferred = guild.get_channel_or_thread(preferred_channel_id) if preferred_channel_id else None
        if isinstance(preferred, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)):
            if MeetingMinutesManager._can_send(preferred, me):
                return preferred

        if not allow_fallback:
            return None

        if guild.system_channel and (me is None or guild.system_channel.permissions_for(me).send_messages):
            return guild.system_channel

        for ch in guild.text_channels:
            if MeetingMinutesManager._can_send(ch, me):
                return ch
        return None

    @staticmethod
    def build_result_embed(guild: discord.Guild, result: MeetingStopResult) -> discord.Embed:
        started = result.session.started_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        ended = result.ended_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        vc = guild.get_channel(result.session.voice_channel_id)
        vc_name = vc.name if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)) else f"ID:{result.session.voice_channel_id}"
        duration_min = max(1, int((result.ended_at - result.session.started_at).total_seconds() // 60))

        embed = discord.Embed(
            title="議事録",
            description=result.summary,
            color=discord.Color.gold(),
            timestamp=datetime.now(JST),
        )
        embed.add_field(name="VC", value=vc_name, inline=True)
        embed.add_field(name="開始", value=started, inline=True)
        embed.add_field(name="終了", value=ended, inline=True)
        embed.add_field(name="時間", value=f"{duration_min}分", inline=True)
        embed.add_field(name="発話行数", value=str(result.transcript_line_count), inline=True)
        embed.add_field(name="停止理由", value=result.reason, inline=True)
        embed.add_field(name="翻訳", value="有効" if result.translated else "なし", inline=True)
        if result.error:
            embed.add_field(name="補足", value=result.error[:500], inline=False)
        if result.truncated:
            embed.set_footer(text="文字起こしログが長いため一部を省略して要約しました。")
        return embed

    async def _start_recording(self, bot: commands.Bot, voice_channel: VoiceLikeChannel) -> _RecordingRuntime:
        runtime = _RecordingRuntime()

        try:
            vr = importlib.import_module("discord.ext.voice_recv")
        except Exception:
            runtime.warning = "音声受信ライブラリ未導入のため、音声文字起こしは無効です。"
            return runtime

        recv_client_cls = getattr(vr, "VoiceRecvClient", None)
        audio_sink_cls = getattr(vr, "AudioSink", object)
        if recv_client_cls is None:
            runtime.warning = "voice_recv の VoiceRecvClient が見つからず、録音を開始できません。"
            return runtime

        chunks = runtime.chunks
        max_total_mb = int(_settings.get("meeting.audio_max_total_mb", 64))
        max_user_mb = int(_settings.get("meeting.audio_max_user_mb", 8))
        runtime.max_total_bytes = max(1, max_total_mb) * 1024 * 1024
        runtime.max_user_bytes = max(1, max_user_mb) * 1024 * 1024
        min_phrase_bytes = self._realtime_min_audio_bytes(voice_channel.guild.id)

        class _Sink(audio_sink_cls):  # type: ignore[misc, valid-type]
            def __init__(self):
                try:
                    super().__init__()
                except Exception:
                    pass

            def wants_opus(self) -> bool:
                # PCM を前提に Whisper へ流す
                return False

            def cleanup(self):
                return None

            def write(self, user, data):
                if runtime.dropped:
                    return
                uid = getattr(user, "id", 0) or 0
                pcm = getattr(data, "pcm", None)
                if not pcm:
                    return

                # Raspberry Pi 向け: メモリ上限を超える場合は録音取り込みを止める
                total = sum(len(v) for v in chunks.values())
                cur_user = len(chunks.get(uid, b""))
                if total + len(pcm) > runtime.max_total_bytes or cur_user + len(pcm) > runtime.max_user_bytes:
                    runtime.dropped = True
                    runtime.warning = (
                        "録音データが上限を超えたため途中で打ち切りました。"
                        f"(total={runtime.max_total_bytes//(1024*1024)}MB, user={runtime.max_user_bytes//(1024*1024)}MB)"
                    )
                    return
                if uid not in chunks:
                    chunks[uid] = bytearray()
                chunks[uid].extend(pcm)
                if uid not in runtime.phrase_chunks:
                    runtime.phrase_chunks[uid] = bytearray()
                runtime.phrase_chunks[uid].extend(pcm)

            @audio_sink_cls.listener()
            def on_voice_member_speaking_stop(self, member) -> None:
                if member is None or runtime.phrase_queue is None:
                    return
                uid = getattr(member, "id", 0) or 0
                phrase = runtime.phrase_chunks.pop(uid, None)
                if not phrase or len(phrase) < min_phrase_bytes:
                    return
                bot.loop.call_soon_threadsafe(runtime.phrase_queue.put_nowait, (uid, bytes(phrase)))

            @audio_sink_cls.listener()
            def on_voice_member_disconnect(self, member, _ssrc=None) -> None:
                if member is None or runtime.phrase_queue is None:
                    return
                uid = getattr(member, "id", 0) or 0
                phrase = runtime.phrase_chunks.pop(uid, None)
                if not phrase or len(phrase) < min_phrase_bytes:
                    return
                bot.loop.call_soon_threadsafe(runtime.phrase_queue.put_nowait, (uid, bytes(phrase)))

        try:
            vc = await voice_channel.connect(cls=recv_client_cls, self_deaf=False, reconnect=False)
            sink = _Sink()
            listen = getattr(vc, "listen", None)
            if callable(listen):
                listen(sink)
                runtime.voice_client = vc
                runtime.sink = sink
            else:
                runtime.warning = "voice_recv の listen API が見つからず、録音を開始できません。"
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
        except Exception as e:
            runtime.warning = f"録音開始に失敗しました: {e}"

        return runtime

    async def _stop_recording_and_transcribe(self, runtime: _RecordingRuntime) -> tuple[list[str], str]:
        warning = runtime.warning
        vc = runtime.voice_client
        if vc is not None:
            try:
                stop = getattr(vc, "stop_listening", None)
                if callable(stop):
                    stop()
            except Exception:
                pass
            try:
                disc = getattr(vc, "disconnect", None)
                if callable(disc):
                    out = disc(force=True)
                    if hasattr(out, "__await__"):
                        await out
            except Exception:
                pass

        if not runtime.chunks:
            return [], warning

        try:
            self._get_whisper_model(0)
        except Exception:
            warn = "faster-whisper 未導入のため文字起こしできません。"
            return [], f"{warning} {warn}".strip()

        lines: list[str] = []
        try:
            lines = await asyncio.to_thread(
                self._transcribe_chunk_map,
                {uid: bytes(pcm) for uid, pcm in runtime.chunks.items()},
                0,
            )
        except Exception as e:
            return [], f"{warning} 文字起こし処理に失敗: {e}".strip()

        return lines, warning

    @staticmethod
    def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int, sample_width: int) -> bytes:
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return bio.getvalue()
