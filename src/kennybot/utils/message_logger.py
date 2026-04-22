from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from src.kennybot.utils.paths import ALL_EVENTS_LOG


logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))


def _append_line(line: str) -> None:
    try:
        ALL_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALL_EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except Exception:
        logger.exception("Failed to write message log")


def _timestamp() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _format_common_prefix(kind: str, msg: Any | None = None) -> str:
    channel_id = getattr(getattr(msg, "channel", None), "id", 0) if msg is not None else 0
    guild_id = getattr(getattr(msg, "guild", None), "id", 0) if msg is not None else 0
    message_id = getattr(msg, "id", 0) if msg is not None else 0
    return f"[{_timestamp()}] [{kind}] guild={guild_id} channel={channel_id} message={message_id}"


def log_user_message(msg: Any) -> None:
    author = getattr(msg, "author", None)
    author_name = getattr(author, "display_name", None) or getattr(author, "name", "unknown")
    author_id = getattr(author, "id", 0)
    content = getattr(msg, "content", "") or ""
    _append_line(
        f"{_format_common_prefix('USER', msg)} author={author_name} author_id={author_id} content={content!r}"
    )


def log_ai_output(
    author: Any,
    *,
    response: str,
    model: str,
    msg: Any | None = None,
    error: str | None = None,
    references: list[str] | None = None,
    web_queries: list[str] | None = None,
) -> None:
    author_name = getattr(author, "display_name", None) or getattr(author, "name", "unknown")
    author_id = getattr(author, "id", 0)
    normalized_references = [str(ref).strip() for ref in references or [] if str(ref).strip()]
    web_used = any(
        ref.startswith("tool:web_search")
        or ref.startswith("tool:web_fetch")
        or ref.startswith("source:web_search")
        or ref.startswith("method:")
        or ref.startswith("web_search")
        or ref.startswith("web_fetch")
        for ref in normalized_references
    )
    parts = [
        _format_common_prefix("AI", msg),
        f"author={author_name}",
        f"author_id={author_id}",
        f"model={model}",
        f"response={response!r}",
        f"web_used={web_used}",
    ]
    if normalized_references:
        parts.append(f"references={normalized_references!r}")
    normalized_queries = [str(query).strip() for query in web_queries or [] if str(query).strip()]
    if normalized_queries:
        parts.append(f"web_queries={normalized_queries!r}")
    if error:
        parts.append(f"error={error!r}")
    line = " ".join(parts)
    _append_line(line)


def log_system_event(
    title: str,
    *,
    description: str = "",
    msg: Any | None = None,
    level: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    parts = [
        _format_common_prefix("SYSTEM", msg),
        f"level={level}",
        f"title={title}",
        f"description={description!r}",
    ]
    if details:
        parts.append(f"details={details!r}")
    line = " ".join(parts)
    _append_line(line)
