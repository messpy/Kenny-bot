import asyncio
from datetime import datetime, timezone
import sys
import tempfile
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from unittest.mock import Mock, patch


discord_module = sys.modules.get("discord")
if discord_module is not None:
    discord_module.__path__ = getattr(discord_module, "__path__", [])
    ext_module = sys.modules.get("discord.ext")
    if ext_module is None:
        ext_module = types.ModuleType("discord.ext")
        ext_module.__path__ = []
        sys.modules["discord.ext"] = ext_module
        discord_module.ext = ext_module
    commands_module = sys.modules.get("discord.ext.commands")
    if commands_module is None:
        commands_module = types.ModuleType("discord.ext.commands")

        class _Cog:
            @classmethod
            def listener(cls, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

        commands_module.Cog = _Cog
        commands_module.Bot = object
        sys.modules["discord.ext.commands"] = commands_module
        ext_module.commands = commands_module
    utils_module = sys.modules.get("discord.utils")
    if utils_module is None:
        utils_module = types.ModuleType("discord.utils")
        sys.modules["discord.utils"] = utils_module
        discord_module.utils = utils_module
    if not hasattr(utils_module, "get"):
        utils_module.get = lambda *args, **kwargs: None
    if not hasattr(utils_module, "utcnow"):
        utils_module.utcnow = lambda: datetime.now(timezone.utc)

from src.kennybot.features.voice.meeting_minutes import MeetingMinutesManager, MeetingSession, _RecordingRuntime


class MeetingMinutesBackendTests(IsolatedAsyncioTestCase):
    async def test_realtime_flush_can_run_repeatedly(self) -> None:
        manager = MeetingMinutesManager()
        runtime = _RecordingRuntime(
            phrase_queue=asyncio.Queue(),
            realtime_live_enabled=True,
        )
        bot = SimpleNamespace(loop=asyncio.get_running_loop())
        manager._sessions[456] = MeetingSession(
            guild_id=456,
            voice_channel_id=789,
            started_by_id=123,
            started_at=datetime.now(timezone.utc),
            runtime=runtime,
        )

        runtime.phrase_chunks[123] = bytearray(b"a" * 24000)
        first = manager._schedule_realtime_phrase_flush(bot, 456, 123, runtime, 0.05)
        replacement = manager._schedule_realtime_phrase_flush(bot, 456, 123, runtime, 0)
        self.assertIsNot(first, replacement)
        await asyncio.sleep(0)
        self.assertTrue(first.cancelled())
        await replacement
        await asyncio.sleep(0)
        self.assertEqual(await runtime.phrase_queue.get(), (123, b"a" * 24000))

        runtime.phrase_chunks[123] = bytearray(b"b" * 24000)
        second = manager._schedule_realtime_phrase_flush(bot, 456, 123, runtime, 0)
        self.assertIsNot(second, first)
        await second
        self.assertEqual(await runtime.phrase_queue.get(), (123, b"b" * 24000))

    def test_repeated_substring_transcript_is_rejected(self) -> None:
        manager = MeetingMinutesManager()
        repeated = "スタッフの" * 20

        self.assertEqual(manager._sanitize_transcript_text(repeated), "")

    def test_normal_repeated_words_are_not_overfiltered(self) -> None:
        manager = MeetingMinutesManager()
        normal = "スタッフの担当者と次回の予定を確認します"

        self.assertEqual(manager._sanitize_transcript_text(normal), normal)

    def test_transcribe_wav_normalizes_non_discord_audio_format(self) -> None:
        manager = MeetingMinutesManager()
        manager._transcribe_chunk_map = Mock(return_value=["user:0 テスト"])  # type: ignore[method-assign]
        normalized_pcm = b"\x01\x00" * 400

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "mono-24k.wav"
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(b"\x00\x00" * 200)

            completed = SimpleNamespace(stdout=normalized_pcm)
            with patch("src.kennybot.features.voice.meeting_minutes.subprocess.run", return_value=completed) as run:
                lines = manager._transcribe_wav_file(wav_path, 123)

        self.assertEqual(lines, ["user:0 テスト"])
        run.assert_called_once()
        manager._transcribe_chunk_map.assert_called_once_with({0: normalized_pcm}, 123, None, None)

    def test_google_web_empty_result_falls_back_to_whisper(self) -> None:
        manager = MeetingMinutesManager()
        manager._resolve_transcription_provider = lambda *_args, **_kwargs: "google_web"  # type: ignore[method-assign]
        manager._transcribe_chunk_map_with_google_web = Mock(return_value=[])  # type: ignore[method-assign]
        manager._preprocess_pcm_for_stt = Mock(return_value=(b"\x01\x00" * 16000, 16000, 1))  # type: ignore[method-assign]
        manager._looks_like_whisper_hallucination = Mock(return_value=False)  # type: ignore[method-assign]
        segment = SimpleNamespace(text=" テスト発話 ")
        model = SimpleNamespace(transcribe=Mock(return_value=([segment], None)))
        manager._get_whisper_model = Mock(return_value=model)  # type: ignore[method-assign]

        lines = manager._transcribe_chunk_map({123: b"pcm"}, 456)

        self.assertEqual(lines, ["user:123 テスト発話"])
        manager._get_whisper_model.assert_called_once()

    def test_fast_fallback_uses_greedy_whisper_decoding(self) -> None:
        manager = MeetingMinutesManager()
        manager._resolve_transcription_provider = lambda *_args, **_kwargs: "google_web"  # type: ignore[method-assign]
        manager._transcribe_chunk_map_with_google_web = Mock(return_value=[])  # type: ignore[method-assign]
        manager._preprocess_pcm_for_stt = Mock(return_value=(b"\x01\x00" * 16000, 16000, 1))  # type: ignore[method-assign]
        manager._looks_like_whisper_hallucination = Mock(return_value=False)  # type: ignore[method-assign]
        model = SimpleNamespace(transcribe=Mock(return_value=([SimpleNamespace(text=" テスト ")], None)))
        manager._get_whisper_model = Mock(return_value=model)  # type: ignore[method-assign]

        lines = manager._transcribe_chunk_map({123: b"pcm"}, 456, whisper_model="tiny", fast=True)

        self.assertEqual(lines, ["user:123 テスト"])
        manager._get_whisper_model.assert_called_once_with(456, "tiny")
        kwargs = model.transcribe.call_args.kwargs
        self.assertEqual(kwargs["beam_size"], 1)
        self.assertEqual(kwargs["best_of"], 1)

    async def test_auto_backend_uses_external_recorder_when_available(self) -> None:
        manager = MeetingMinutesManager()
        guild = SimpleNamespace(id=123)
        voice_channel = SimpleNamespace(guild=guild)
        external = _RecordingRuntime(recorder_process=object())
        internal = _RecordingRuntime(voice_client=object())
        manager._recording_backend = lambda _guild_id: "auto"  # type: ignore[method-assign]
        manager._start_external_recorder = AsyncMock(return_value=external)  # type: ignore[method-assign]
        manager._start_internal_recording = AsyncMock(return_value=internal)  # type: ignore[method-assign]

        runtime = await manager._start_recording(SimpleNamespace(), voice_channel)

        self.assertIs(runtime, external)
        manager._start_internal_recording.assert_not_awaited()

    async def test_auto_backend_falls_back_to_internal_recording(self) -> None:
        manager = MeetingMinutesManager()
        guild = SimpleNamespace(id=123)
        voice_channel = SimpleNamespace(guild=guild)
        external = _RecordingRuntime(warning="external failed")
        internal = _RecordingRuntime(voice_client=object(), warning="")
        manager._recording_backend = lambda _guild_id: "auto"  # type: ignore[method-assign]
        manager._start_external_recorder = AsyncMock(return_value=external)  # type: ignore[method-assign]
        manager._start_internal_recording = AsyncMock(return_value=internal)  # type: ignore[method-assign]

        runtime = await manager._start_recording(SimpleNamespace(), voice_channel)

        self.assertIs(runtime, internal)
        self.assertIn("外部録音は使えませんでした", runtime.warning)

    async def test_auto_backend_does_not_treat_exited_external_process_as_started(self) -> None:
        manager = MeetingMinutesManager()
        guild = SimpleNamespace(id=123)
        voice_channel = SimpleNamespace(guild=guild)
        external = _RecordingRuntime(recorder_process=SimpleNamespace(returncode=1), warning="external exited")
        internal = _RecordingRuntime(voice_client=object())
        manager._recording_backend = lambda _guild_id: "auto"  # type: ignore[method-assign]
        manager._start_external_recorder = AsyncMock(return_value=external)  # type: ignore[method-assign]
        manager._start_internal_recording = AsyncMock(return_value=internal)  # type: ignore[method-assign]

        runtime = await manager._start_recording(SimpleNamespace(), voice_channel)

        self.assertIs(runtime, internal)

    async def test_start_session_starts_external_recorder_watch_task(self) -> None:
        manager = MeetingMinutesManager()
        guild = SimpleNamespace(id=123)
        voice_channel = SimpleNamespace(id=456, name="VC", guild=guild)
        runtime = _RecordingRuntime(recorder_process=object())
        manager.warmup_transcriber = lambda *_args, **_kwargs: "ready"  # type: ignore[method-assign]
        manager._start_recording = AsyncMock(return_value=runtime)  # type: ignore[method-assign]

        async def wait_forever(*_args, **_kwargs):
            await asyncio.Event().wait()

        manager._watch_external_recorder = wait_forever  # type: ignore[method-assign]

        ok, message = await manager.start_session(
            bot=SimpleNamespace(),
            guild=guild,
            voice_channel=voice_channel,
            started_by_id=789,
            announce_channel_id=111,
        )

        self.assertTrue(ok)
        self.assertIn("外部レコーダー", message)
        self.assertIsNotNone(runtime.recorder_watch_task)
        runtime.recorder_watch_task.cancel()
        try:
            await runtime.recorder_watch_task
        except asyncio.CancelledError:
            pass

    async def test_stop_recording_disconnects_internal_voice_client(self) -> None:
        manager = MeetingMinutesManager()
        voice_client = SimpleNamespace(
            stop_listening=Mock(),
            disconnect=AsyncMock(),
        )
        runtime = _RecordingRuntime(voice_client=voice_client)

        lines, warning = await manager._stop_recording_and_transcribe(runtime, 123)

        self.assertEqual(lines, [])
        self.assertEqual(warning, "")
        voice_client.stop_listening.assert_called_once()
        voice_client.disconnect.assert_awaited_once_with(force=True)

    async def test_playback_stop_skips_transcription_and_creates_mp3(self) -> None:
        manager = MeetingMinutesManager()
        voice_client = SimpleNamespace(
            stop_listening=Mock(),
            disconnect=AsyncMock(),
        )
        runtime = _RecordingRuntime(
            voice_client=voice_client,
            chunks={123: bytearray(b"\x01\x00" * 100)},
        )
        guild = SimpleNamespace(id=456)
        manager._sessions[guild.id] = MeetingSession(
            guild_id=guild.id,
            voice_channel_id=789,
            started_by_id=123,
            started_at=datetime.now(timezone.utc),
            runtime=runtime,
        )
        manager._transcribe_chunk_map = Mock(side_effect=AssertionError("STT must not run"))  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "recording.wav"
            wav_path.write_bytes(b"wav")
            manager._dump_debug_audio = Mock(return_value=[str(wav_path)])  # type: ignore[method-assign]

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"mp3")
                return SimpleNamespace(returncode=0)

            with patch("src.kennybot.features.voice.meeting_minutes.subprocess.run", side_effect=fake_ffmpeg):
                stopped = await manager.stop_session_for_playback(guild)

            self.assertIsNotNone(stopped)
            _session, audio_path, warning = stopped
            self.assertEqual(audio_path, wav_path.with_suffix(".mp3"))
            self.assertEqual(warning, "")
            self.assertTrue(audio_path.exists())

        manager._transcribe_chunk_map.assert_not_called()
        voice_client.stop_listening.assert_called_once()
        voice_client.disconnect.assert_awaited_once_with(force=True)

    async def test_start_session_does_not_enable_realtime_posting_by_default(self) -> None:
        manager = MeetingMinutesManager()
        guild = SimpleNamespace(id=123)
        voice_channel = SimpleNamespace(id=456, name="VC", guild=guild)
        runtime = _RecordingRuntime(voice_client=object())
        manager.warmup_transcriber = lambda *_args, **_kwargs: "ready"  # type: ignore[method-assign]
        manager._start_recording = AsyncMock(return_value=runtime)  # type: ignore[method-assign]

        async def wait_forever(*_args, **_kwargs):
            await asyncio.Event().wait()

        manager._run_realtime_updates = wait_forever  # type: ignore[method-assign]

        ok, message = await manager.start_session(
            bot=SimpleNamespace(),
            guild=guild,
            voice_channel=voice_channel,
            started_by_id=789,
            announce_channel_id=111,
        )

        self.assertTrue(ok)
        self.assertFalse(runtime.realtime_live_enabled)
        self.assertIsNone(runtime.realtime_task)
        self.assertIn("停止時に文字起こし・要約します。", message)
        self.assertNotIn("リアル文字起こし投稿", message)
