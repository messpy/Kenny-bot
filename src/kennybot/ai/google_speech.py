"""Compatibility shim for legacy speech transcription imports."""

from src.kennybot.features.voice.google_speech import GoogleSpeechClient, GoogleSpeechConfig

__all__ = ["GoogleSpeechClient", "GoogleSpeechConfig"]
