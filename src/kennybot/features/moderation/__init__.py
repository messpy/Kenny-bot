"""モデレーション・スパム対策機能。"""

from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "ActionResult": "src.kennybot.features.moderation.mod_actions",
    "ModActions": "src.kennybot.features.moderation.mod_actions",
    "ModPanel": "src.kennybot.features.moderation.mod_panel",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(module_name)
    return getattr(module, name)
