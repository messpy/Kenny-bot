from __future__ import annotations

import copy
import json
import re
from typing import Any

from src.kennybot.utils.text import strip_ansi_and_ctrl

DEFAULT_PLANNER_PLAN: dict[str, object] = {
    "serverinfo": False,
    "rag": {
        "enabled": False,
        "query": None,
        "limit": 0,
    },
    "web_search": {
        "enabled": False,
        "query": None,
        "limit": 0,
    },
    "response_mode": "normal",
    "reason": "",
}

_SEARCH_QUERY_MAX_LEN = 300
_SEARCH_QUERY_LEAK_MARKERS = (
    "system_message",
    "retrieval_plan_prompt",
    "prompt_template",
    "channel_profile_prompt",
    "history_context",
    "channel_profile_block",
    "latest_message",
    "user_message",
    "final prompt",
    "chat messages",
    "tool calls",
    "internal json",
    "planner json",
    "response_mode",
    "web_search.query",
    "planner_prompt",
    "responder_prompt",
    "以下は discord",
    "出力は json のみ",
    "説明文、markdown、前置き",
)


def parse_json_payload(raw: object | None) -> object | None:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    text = strip_ansi_and_ctrl(str(raw or "")).strip()
    if not text:
        return None
    candidates = [text]
    for start, end in (("{", "}"), ("[", "]")):
        left = text.find(start)
        right = text.rfind(end)
        if left != -1 and right != -1 and right > left:
            candidates.append(text[left : right + 1].strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            try:
                return int(text)
            except Exception:
                return default
    return default


def _coerce_query(value: object) -> str | None:
    if value is None:
        return None
    text = strip_ansi_and_ctrl(str(value)).strip()
    if not text:
        return None
    return re.sub(r"\s+", " ", text)


def normalize_planner_plan(payload: object | None) -> dict[str, object]:
    plan = copy.deepcopy(DEFAULT_PLANNER_PLAN)
    if not isinstance(payload, dict):
        return plan

    if "serverinfo" in payload:
        plan["serverinfo"] = _coerce_bool(payload.get("serverinfo"), False)

    rag = payload.get("rag")
    if isinstance(rag, dict):
        plan["rag"] = {
            "enabled": _coerce_bool(rag.get("enabled"), False),
            "query": _coerce_query(rag.get("query")),
            "limit": max(0, min(_coerce_int(rag.get("limit"), 0), 8)),
        }

    web_search = payload.get("web_search")
    if isinstance(web_search, dict):
        plan["web_search"] = {
            "enabled": _coerce_bool(web_search.get("enabled"), False),
            "query": _coerce_query(web_search.get("query")),
            "limit": max(0, min(_coerce_int(web_search.get("limit"), 0), 8)),
        }

    response_mode = str(payload.get("response_mode") or "").strip().lower()
    allowed_modes = {
        "normal",
        "chat",
        "server_description",
        "current_info",
        "capability",
    }
    if response_mode in allowed_modes:
        plan["response_mode"] = response_mode

    reason = str(payload.get("reason") or "").strip()
    if reason:
        plan["reason"] = reason[:500]

    return plan


def _contains_prompt_leakage(text: str) -> bool:
    normalized = strip_ansi_and_ctrl(text or "").strip().lower()
    if not normalized:
        return False
    if len(normalized) > _SEARCH_QUERY_MAX_LEN:
        return True
    if any(marker in normalized for marker in _SEARCH_QUERY_LEAK_MARKERS):
        return True
    leak_signals = (
        normalized.count("[") >= 3 and normalized.count("]") >= 3,
        normalized.count("\n") >= 3,
        "json only" in normalized and "discord" in normalized,
    )
    return any(leak_signals)


def validate_search_query(query: object, *, latest_message: str = "") -> tuple[bool, str, str]:
    text = _coerce_query(query) or ""
    if not text:
        return False, "empty_query", ""
    if len(text) > _SEARCH_QUERY_MAX_LEN:
        return False, "query_too_long", ""

    combined = f"{text}\n{latest_message}".strip()
    if _contains_prompt_leakage(combined):
        return False, "prompt_leakage", ""

    if not any(ch.isalnum() for ch in text):
        return False, "query_not_searchable", ""

    return True, "ok", text
