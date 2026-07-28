"""モデレーション・スパム対策機能。"""

from __future__ import annotations

from importlib import import_module

from src.kennybot.features.moderation.mod_actions import ActionResult, ModActions


__all__ = [
    "ActionResult",
    "ModActions",
    "ModPanel",
]


def __getattr__(name: str):
    if name == "ModPanel":
        module = import_module("src.kennybot.features.moderation.mod_panel")
        return getattr(module, name)
    raise AttributeError(name)
