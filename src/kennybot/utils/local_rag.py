"""Compatibility module alias for local RAG utilities."""

from importlib import import_module
import sys

_module = import_module("src.kennybot.features.search.local_rag")
sys.modules[__name__] = _module
