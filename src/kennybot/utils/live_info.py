"""Compatibility module alias for search live info utilities."""

from importlib import import_module
import sys

_module = import_module("src.kennybot.features.search.live_info")
sys.modules[__name__] = _module
