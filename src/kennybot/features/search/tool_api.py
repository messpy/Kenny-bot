from __future__ import annotations

from pathlib import Path
from typing import Any

from src.kennybot.features.search.live_info import ExternalContext, LiveInfoService
from src.kennybot.features.search.local_rag import LocalRAG, RagChunk
from src.kennybot.features.search.profile_preview import (
    build_channel_profile_preview,
    build_profile_chunks,
    format_profile_chunks,
)
from src.kennybot.features.search.profile_preview_api import parse_json_payload
from src.kennybot.storage.server_repository import get_server_registry
from src.kennybot.utils.text import sanitize_user_visible_error, strip_ansi_and_ctrl


DEFAULT_TOOL_MENU: list[dict[str, object]] = [
    {
        "name": "server_stats",
        "description": "サーバー主、概算メンバー数、保存済みログ上の発言数上位などの統計を返す",
        "priority": 1,
        "input": {
            "guild_id": "int",
            "channel_id": "int|null",
            "scope": "guild|channel",
            "member_count": "int|null",
            "owner_id": "int|null",
            "owner_name": "str|null",
        },
    },
    {
        "name": "serverinfo",
        "description": "サーバー説明、目的、参加方法、Bot の使い方など、サーバー固有情報を返す",
        "priority": 2,
        "input": {
            "guild_id": "int",
            "channel_id": "int",
            "scope": "auto|guild|channel|legacy_channel",
            "question": "str",
            "limit": "int",
            "max_chars": "int",
        },
    },
    {
        "name": "rag",
        "description": "過去ログ、ナレッジ、Bot 仕様、サーバー固有の文書検索を返す",
        "priority": 3,
        "input": {
            "guild_id": "int",
            "channel_id": "int",
            "query": "str",
            "limit": "int",
            "capability_only": "bool",
            "channel_only": "bool",
        },
    },
    {
        "name": "web_search",
        "description": "最新情報、時事、天気、価格、在庫、CVE、API 仕様などの外部検索結果を返す",
        "priority": 4,
        "input": {
            "query": "str",
            "news_only": "bool",
            "limit": "int",
        },
    },
]


def _get_value(payload: dict[str, Any], name: str, default: Any, args: Any | None = None) -> Any:
    if name in payload and payload[name] is not None:
        return payload[name]
    if args is not None and hasattr(args, name):
        value = getattr(args, name)
        if value is not None:
            return value
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


def _coerce_query(value: object) -> str | None:
    if value is None:
        return None
    text = strip_ansi_and_ctrl(str(value)).strip()
    if not text:
        return None
    return " ".join(text.split())


def list_tool_menu() -> dict[str, Any]:
    return {"ok": True, "tools": DEFAULT_TOOL_MENU}


def _serialize_rag_chunk(chunk: RagChunk) -> dict[str, Any]:
    return {
        "source": chunk.source,
        "title": chunk.title,
        "body": chunk.body,
    }


def _serialize_web_item(item: Any) -> dict[str, Any]:
    title = getattr(item, "title", "")
    url = getattr(item, "url", "")
    snippet = getattr(item, "snippet", "")
    date = getattr(item, "date", None)
    source = getattr(item, "source", None)
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "date": date,
        "source": source,
    }


def _serialize_context(item: ExternalContext) -> dict[str, Any]:
    return {
        "label": item.label,
        "body": item.body,
    }


def build_serverinfo_response(
    *,
    root: Path,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
) -> dict[str, Any]:
    data = payload or {}
    guild_id = _get_value(data, "guild_id", None, args=args)
    channel_id = _get_value(data, "channel_id", None, args=args)
    scope = str(_get_value(data, "scope", "auto", args=args))
    question = str(_get_value(data, "question", "このサーバーはなにするところ？", args=args))
    limit = _coerce_int(_get_value(data, "limit", 6, args=args), 6)
    max_chars = _coerce_int(_get_value(data, "max_chars", 2600, args=args), 2600)

    preview = build_channel_profile_preview(
        root=root,
        guild_id=None if guild_id is None else int(guild_id),
        channel_id=None if channel_id is None else int(channel_id),
        scope=scope,
        question=question,
        limit=limit,
        max_chars=max_chars,
    )
    chunks = build_profile_chunks(
        root=root,
        guild_id=None if guild_id is None else int(guild_id),
        channel_id=None if channel_id is None else int(channel_id),
        scope=scope,
        limit=limit,
    )
    response = {
        "ok": True,
        "tool": "serverinfo",
        "guild_id": guild_id,
        "channel_id": channel_id,
        "scope": scope,
        "question": question,
        "chunks": [_serialize_rag_chunk(chunk) for chunk in chunks],
        "profile": preview.get("profile", ""),
        "profile_summary": preview.get("profile_summary", ""),
        "summary": preview.get("answer", ""),
    }
    if not chunks:
        response["ok"] = False
        response["error"] = "not_found"
    return response


def build_server_stats_response(
    *,
    root: Path,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
) -> dict[str, Any]:
    del root
    data = payload or {}
    guild_id = _get_value(data, "guild_id", None, args=args)
    channel_id = _get_value(data, "channel_id", None, args=args)
    scope = str(_get_value(data, "scope", "guild", args=args) or "guild").strip().lower()
    member_count = _get_value(data, "member_count", None, args=args)
    owner_id = _get_value(data, "owner_id", None, args=args)
    owner_name = _get_value(data, "owner_name", None, args=args)

    numeric_guild_id = None if guild_id is None else int(guild_id)
    numeric_channel_id = None if channel_id is None else int(channel_id)
    registry = get_server_registry()
    rows = registry.list_message_logs_any(
        guild_id=numeric_guild_id,
        channel_id=numeric_channel_id if scope == "channel" else None,
        limit=2000,
    )

    counts: dict[int, dict[str, Any]] = {}
    for row in rows:
        author_id = int(row.get("author_id") or 0)
        if author_id <= 0:
            continue
        meta = row.get("metadata") or {}
        if isinstance(meta, dict) and bool(meta.get("is_bot")):
            continue
        entry = counts.setdefault(
            author_id,
            {
                "author_id": author_id,
                "author": str(row.get("author") or author_id),
                "count": 0,
            },
        )
        entry["count"] = int(entry["count"]) + 1

    top_talkers = sorted(
        counts.values(),
        key=lambda item: (-int(item["count"]), str(item["author"])),
    )[:5]

    response = {
        "ok": True,
        "tool": "server_stats",
        "guild_id": guild_id,
        "channel_id": channel_id,
        "scope": scope,
        "member_count": _coerce_int(member_count, 0) if member_count is not None else None,
        "owner_id": _coerce_int(owner_id, 0) if owner_id is not None else None,
        "owner_name": _coerce_query(owner_name),
        "top_talkers": top_talkers,
        "sample_size": len(rows),
    }
    return response


def build_rag_response(
    *,
    root: Path,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
    rag: LocalRAG | None = None,
) -> dict[str, Any]:
    data = payload or {}
    query = _coerce_query(_get_value(data, "query", None, args=args))
    if not query:
        return {"ok": False, "tool": "rag", "error": "empty_query"}

    guild_id = _get_value(data, "guild_id", None, args=args)
    channel_id = _get_value(data, "channel_id", None, args=args)
    limit = max(1, min(_coerce_int(_get_value(data, "limit", 4, args=args), 4), 12))
    capability_only = _coerce_bool(_get_value(data, "capability_only", False, args=args), False)
    channel_only = _coerce_bool(_get_value(data, "channel_only", False, args=args), False)

    local_rag = rag or LocalRAG(root)
    chunks = local_rag.retrieve(
        query,
        limit=limit,
        capability_only=capability_only,
        guild_id=None if guild_id is None else int(guild_id),
        channel_id=None if channel_id is None else int(channel_id),
        channel_only=channel_only,
    )
    response_chunks = [_serialize_rag_chunk(chunk) for chunk in chunks]
    response = {
        "ok": True,
        "tool": "rag",
        "query": query,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "limit": limit,
        "capability_only": capability_only,
        "channel_only": channel_only,
        "chunks": response_chunks,
        "context": format_profile_chunks(chunks, max_chars=2600),
    }
    if not chunks:
        response["ok"] = False
        response["error"] = "not_found"
    return response


def build_web_search_response(
    *,
    root: Path,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
    searcher: Any | None = None,
    live_info: LiveInfoService | None = None,
) -> dict[str, Any]:
    del root
    data = payload or {}
    query = _coerce_query(_get_value(data, "query", None, args=args))
    if not query:
        return {"ok": False, "tool": "web_search", "error": "empty_query"}

    limit = max(1, min(_coerce_int(_get_value(data, "limit", 5, args=args), 5), 10))
    news_only = _coerce_bool(_get_value(data, "news_only", False, args=args), False)
    live_info_service = live_info or LiveInfoService()
    contexts = live_info_service.build_context(query) if live_info_service.needs_external_context(query) else []
    if searcher is None:
        try:
            from src.kennybot.features.search import DuckDuckGoSearch, SearchConfig
        except Exception as exc:
            return {
                "ok": False,
                "tool": "web_search",
                "error": "search_backend_unavailable",
                "detail": sanitize_user_visible_error(exc),
                "query": query,
            }
        searcher = DuckDuckGoSearch(SearchConfig(top_n=limit, max_results=max(10, limit), news_only=news_only))
    items = searcher.search(query, news_only=news_only)
    result_items = [_serialize_web_item(item) for item in items[:limit]]
    context_lines = []
    for item in items[:limit]:
        lines = [f"- {item.title}", f"  {item.url}"]
        if item.snippet.strip():
            lines.append(f"  {item.snippet.strip()}")
        context_lines.append("\n".join(lines))
    response = {
        "ok": True,
        "tool": "web_search",
        "query": query,
        "limit": limit,
        "news_only": news_only,
        "contexts": [_serialize_context(item) for item in contexts],
        "items": result_items,
        "context": "\n\n".join(context_lines),
    }
    if not items and not contexts:
        response["ok"] = False
        response["error"] = "not_found"
    return response


def build_tool_response(
    *,
    root: Path,
    tool: str,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
    rag: LocalRAG | None = None,
    searcher: DuckDuckGoSearch | None = None,
    live_info: LiveInfoService | None = None,
) -> dict[str, Any]:
    normalized_tool = (tool or "").strip().lower()
    if normalized_tool == "server_stats":
        return build_server_stats_response(root=root, payload=payload, args=args)
    if normalized_tool == "serverinfo":
        return build_serverinfo_response(root=root, payload=payload, args=args)
    if normalized_tool == "rag":
        return build_rag_response(root=root, payload=payload, args=args, rag=rag)
    if normalized_tool == "web_search":
        return build_web_search_response(root=root, payload=payload, args=args, searcher=searcher, live_info=live_info)
    return {"ok": False, "tool": normalized_tool or tool, "error": "unknown_tool"}
