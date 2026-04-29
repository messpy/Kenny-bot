"""音声・議事録・TTS 機能。"""

from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "GoogleSpeechClient": "src.kennybot.features.voice.google_speech",
    "GoogleSpeechConfig": "src.kennybot.features.voice.google_speech",
    "MeetingMinutesManager": "src.kennybot.features.voice.meeting_minutes",
    "MeetingSession": "src.kennybot.features.voice.meeting_minutes",
    "MeetingStopResult": "src.kennybot.features.voice.meeting_minutes",
    "GuildTtsState": "src.kennybot.features.voice.tts_reader",
    "TTSReader": "src.kennybot.features.voice.tts_reader",
    "VoiceLogger": "src.kennybot.features.voice.voice_logger",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(module_name)
    return getattr(module, name)
