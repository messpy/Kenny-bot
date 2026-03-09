# utils/meeting_minutes.py
# 通話議事録（音声文字起こし + AI要約）

from __future__ import annotations

import importlib
import io
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from utils.runtime_settings import get_settings

JST = timezone(timedelta(hours=9))
_settings = get_settings()


@dataclass
class _RecordingRuntime:
    voice_client: object | None = None
    sink: object | None = None
    chunks: dict[int, bytearray] = field(default_factory=dict)
    warning: str = ""
    max_total_bytes: int = 64 * 1024 * 1024
    max_user_bytes: int = 8 * 1024 * 1024
    dropped: bool = False


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
    error: str = ""


class MeetingMinutesManager:
    """議事録セッション管理"""

    def __init__(self):
        self._sessions: dict[int, MeetingSession] = {}

    def get_session(self, guild_id: int) -> Optional[MeetingSession]:
        return self._sessions.get(guild_id)

    async def start_session(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel,
        started_by_id: int,
        announce_channel_id: int | None = None,
    ) -> tuple[bool, str]:
        if guild.id in self._sessions:
            current = self._sessions[guild.id]
            return False, f"すでに議事録が進行中です（VC ID: {current.voice_channel_id}）。"

        runtime = await self._start_recording(bot, voice_channel)
        self._sessions[guild.id] = MeetingSession(
            guild_id=guild.id,
            voice_channel_id=voice_channel.id,
            started_by_id=started_by_id,
            started_at=discord.utils.utcnow(),
            announce_channel_id=announce_channel_id,
            runtime=runtime,
        )

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
        vc_name = vc.name if isinstance(vc, discord.VoiceChannel) else f"ID:{session.voice_channel_id}"
        duration_min = max(1, int((ended_at - session.started_at).total_seconds() // 60))
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
            f"{transcript}"
        )

        try:
            model_summary = str(_settings.get("ollama.model_summary", "gpt-oss:120b-cloud"))
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
                error=warning or "",
            )
        except Exception as e:
            return MeetingStopResult(
                session=session,
                ended_at=ended_at,
                reason=reason,
                transcript_line_count=transcript_line_count,
                summary="AI要約に失敗しました。文字起こしログの確認が必要です。",
                mention_user_id=mention_uid,
                used_ai=False,
                truncated=truncated,
                error=str(e),
            )

    @staticmethod
    def is_human_empty(channel: discord.VoiceChannel) -> bool:
        humans = [m for m in channel.members if not m.bot]
        return len(humans) == 0

    @staticmethod
    def resolve_announce_channel(guild: discord.Guild, preferred_channel_id: int | None) -> discord.TextChannel | None:
        me = guild.me
        preferred = guild.get_channel(preferred_channel_id) if preferred_channel_id else None
        if isinstance(preferred, discord.TextChannel):
            if me is None or preferred.permissions_for(me).send_messages:
                return preferred

        if guild.system_channel and (me is None or guild.system_channel.permissions_for(me).send_messages):
            return guild.system_channel

        for ch in guild.text_channels:
            if me is None or ch.permissions_for(me).send_messages:
                return ch
        return None

    @staticmethod
    def build_result_embed(guild: discord.Guild, result: MeetingStopResult) -> discord.Embed:
        started = result.session.started_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        ended = result.ended_at.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        vc = guild.get_channel(result.session.voice_channel_id)
        vc_name = vc.name if isinstance(vc, discord.VoiceChannel) else f"ID:{result.session.voice_channel_id}"
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
        if result.error:
            embed.add_field(name="補足", value=result.error[:500], inline=False)
        if result.truncated:
            embed.set_footer(text="文字起こしログが長いため一部を省略して要約しました。")
        return embed

    async def _start_recording(self, bot: commands.Bot, voice_channel: discord.VoiceChannel) -> _RecordingRuntime:
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

        class _Sink(audio_sink_cls):  # type: ignore[misc, valid-type]
            def __init__(self):
                try:
                    super().__init__()
                except Exception:
                    pass

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

        try:
            vc = await voice_channel.connect(cls=recv_client_cls, self_deaf=True)
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
            fw = importlib.import_module("faster_whisper")
            WhisperModel = getattr(fw, "WhisperModel")
        except Exception:
            warn = "faster-whisper 未導入のため文字起こしできません。"
            return [], f"{warning} {warn}".strip()

        lines: list[str] = []
        try:
            whisper_model = str(_settings.get("meeting.whisper_model", "tiny"))
            model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
            for uid, pcm in runtime.chunks.items():
                if not pcm:
                    continue
                wav_bytes = self._pcm_to_wav(bytes(pcm), sample_rate=48000, channels=2, sample_width=2)
                segments, _info = model.transcribe(io.BytesIO(wav_bytes), language="ja")
                text = " ".join([seg.text.strip() for seg in segments if getattr(seg, "text", "").strip()])
                if text:
                    lines.append(f"user:{uid} {text}")
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
