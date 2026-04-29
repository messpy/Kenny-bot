"""Compatibility module alias for profile preview helpers."""

from importlib import import_module
import sys

_module = import_module("src.kennybot.features.search.profile_preview")
sys.modules[__name__] = _module
