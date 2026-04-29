"""Compatibility shim for the voice logger cog."""

from __future__ import annotations

from src.kennybot.features.voice import voice_logger as _voice_logger_impl

send_event_log = _voice_logger_impl.send_event_log


class VoiceLogger(_voice_logger_impl.VoiceLogger):
    async def _handle_voice_join(self, member, channel, guild):
        original = _voice_logger_impl.send_event_log
        _voice_logger_impl.send_event_log = send_event_log
        try:
            return await super()._handle_voice_join(member, channel, guild)
        finally:
            _voice_logger_impl.send_event_log = original

    async def _handle_voice_leave(self, member, channel, guild):
        original = _voice_logger_impl.send_event_log
        _voice_logger_impl.send_event_log = send_event_log
        try:
            return await super()._handle_voice_leave(member, channel, guild)
        finally:
            _voice_logger_impl.send_event_log = original


__all__ = ["VoiceLogger", "send_event_log"]
