"""音声・議事録・TTS 機能。"""

from __future__ import annotations

from src.kennybot.features.voice.google_speech import GoogleSpeechClient, GoogleSpeechConfig
from src.kennybot.features.voice.meeting_minutes import MeetingMinutesManager, MeetingSession, MeetingStopResult
from src.kennybot.features.voice.tts_reader import GuildTtsState, TTSReader
from src.kennybot.features.voice.voice_logger import VoiceLogger


__all__ = [
    "GoogleSpeechClient",
    "GoogleSpeechConfig",
    "MeetingMinutesManager",
    "MeetingSession",
    "MeetingStopResult",
    "GuildTtsState",
    "TTSReader",
    "VoiceLogger",
]
