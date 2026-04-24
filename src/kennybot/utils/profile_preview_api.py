from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_management_log,
    write_jsonl_log,
)


def _get_value(payload: dict[str, Any], name: str, default: Any, args: Any | None = None) -> Any:
    if name in payload and payload[name] is not None:
        return payload[name]
    if args is not None and hasattr(args, name):
        value = getattr(args, name)
        if value is not None:
            return value
    return default


def build_profile_preview_response(
    *,
    root: Path,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
) -> dict[str, Any]:
    data = payload or {}
    preview = build_channel_profile_preview(
        root=Path(_get_value(data, "root", root, args=args)),
        guild_id=_get_value(data, "guild_id", 972052382315855912, args=args),
        channel_id=_get_value(data, "channel_id", 972052382315855912, args=args),
        scope=str(_get_value(data, "scope", "auto", args=args)),
        question=str(_get_value(data, "question", "このサーバーはなにするところ？", args=args)),
        limit=int(_get_value(data, "limit", 6, args=args)),
        max_chars=int(_get_value(data, "max_chars", 2600, args=args)),
    )
    management_log = build_profile_management_log(preview)
    response = dict(preview)
    response["management_log"] = management_log
    emit_log = bool(_get_value(data, "emit_log", False, args=args))
    if emit_log:
        log_file = Path(
            _get_value(
                data,
                "log_file",
                root / "runtime" / "logs" / "profile_preview.log",
                args=args,
            )
        )
        write_jsonl_log(log_file, management_log)
    return response


def parse_json_payload(text: str) -> dict[str, Any]:
    if not text:
        return {}
    loaded = json.loads(text)
    return loaded if isinstance(loaded, dict) else {}
