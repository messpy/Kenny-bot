"""Compatibility module alias for voice meeting minutes."""

from importlib import import_module
import sys

_module = import_module("src.kennybot.features.voice.meeting_minutes")
sys.modules[__name__] = _module
