"""Compatibility module alias for search tool API helpers."""

from importlib import import_module
import sys

_module = import_module("src.kennybot.features.search.tool_api")
sys.modules[__name__] = _module
