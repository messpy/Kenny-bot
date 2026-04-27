from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.kennybot.utils.local_rag import RagChunk, load_rag_chunks_from_directory
from src.kennybot.utils.paths import CHANNEL_RAG_DIR, SERVER_RAG_DIR


GENERIC_TITLE_TOKENS = {"README", "定義", "概要", "紹介", "説明"}
SECTION_PRIORITY = ("定義", "体験内容", "主催", "運営", "活動カテゴリ")
DISPLAY_REPLACEMENTS = (("出典", "出展"),)


def _scoped_directories(
    *,
    root: Path,
    guild_id: int | None,
    channel_id: int | None,
    scope: str,
) -> list[Path]:
    server_channel_root = None
    legacy_channel_root = None
    server_guild_root = None
    legacy_guild_root = None
    if guild_id is not None and channel_id is not None:
        server_channel_root = root / SERVER_RAG_DIR / str(int(guild_id)) / "channels" / str(int(channel_id))
        legacy_channel_root = root / CHANNEL_RAG_DIR / str(int(guild_id)) / "channels" / str(int(channel_id))
    elif channel_id is not None:
        server_channel_root = root / SERVER_RAG_DIR / str(int(channel_id))
        legacy_channel_root = root / CHANNEL_RAG_DIR / str(int(channel_id))

    if guild_id is not None:
        server_guild_root = root / SERVER_RAG_DIR / str(int(guild_id))
        legacy_guild_root = root / CHANNEL_RAG_DIR / str(int(guild_id))

    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope == "guild":
        return [path for path in [server_guild_root, legacy_guild_root] if path is not None]
    if normalized_scope == "channel":
        return [path for path in [server_channel_root, legacy_channel_root] if path is not None]
    if normalized_scope == "legacy_channel":
        if channel_id is None:
            return []
        return [root / CHANNEL_RAG_DIR / str(int(channel_id))]

    out: list[Path] = []
    for path in [server_channel_root, legacy_channel_root, server_guild_root, legacy_guild_root]:
        if path is not None and path not in out:
            out.append(path)
    return out


def build_profile_chunks(
    *,
    root: Path,
    guild_id: int | None = None,
    channel_id: int | None = None,
    scope: str = "auto",
    limit: int = 6,
) -> list[RagChunk]:
    normalized_limit = max(1, min(int(limit or 6), 6))
    for directory in _scoped_directories(
        root=root,
        guild_id=guild_id,
        channel_id=channel_id,
        scope=scope,
    ):
        if directory is None:
            continue
        chunks = load_rag_chunks_from_directory(directory)
        if chunks:
            return chunks[:normalized_limit]
    return []


def format_profile_chunks(chunks: list[RagChunk], max_chars: int = 1800) -> str:
    blocks: list[str] = []
    for chunk in chunks:
        body = chunk.body.strip()
        if max_chars > 0 and len(body) > max_chars:
            body = body[:max_chars] + "\n...(省略)..."
        blocks.append(f"[{chunk.source} / {chunk.title}]\n{body}")
    return "\n\n".join(blocks)


def _first_sentence(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    for separator in ("。", "\n", "!", "！", "?", "？"):
        if separator in cleaned:
            return cleaned.split(separator, 1)[0].strip() + ("。" if separator == "。" else "")
    return cleaned


def _normalize_display_text(text: str) -> str:
    out = text or ""
    for src, dst in DISPLAY_REPLACEMENTS:
        out = out.replace(src, dst)
    return out


def _chunk_matches(chunk: RagChunk, keywords: tuple[str, ...]) -> bool:
    haystack = f"{chunk.title}\n{chunk.body}"
    return any(keyword and keyword in haystack for keyword in keywords)


def _collect_chunks_by_keywords(chunks: list[RagChunk], keywords: tuple[str, ...]) -> list[RagChunk]:
    matched = [chunk for chunk in chunks if _chunk_matches(chunk, keywords)]
    if matched:
        return matched
    return chunks[:1] if chunks else []


def _extract_subject(chunks: list[RagChunk]) -> str:
    for chunk in chunks:
        title = (chunk.title or "").strip()
        if title and title not in GENERIC_TITLE_TOKENS:
            return title
        match = re.search(r"^(.+?)とは[、,]", chunk.body or "")
        if match:
            return match.group(1).strip()
    return ""


def _extract_definition_summary(chunks: list[RagChunk]) -> str:
    for chunk in _collect_chunks_by_keywords(chunks, ("定義", "概要", "紹介", "説明")):
        first = _first_sentence(chunk.body)
        if not first:
            continue
        if "とは" in first:
            suffix = first.split("とは", 1)[1].lstrip("、, ").strip()
            if suffix:
                return suffix
        return first
    return ""


def _collect_bullets(chunks: list[RagChunk], *, max_items: int = 3) -> list[str]:
    items: list[str] = []
    for chunk in chunks:
        for raw_line in chunk.body.splitlines():
            line = raw_line.strip()
            if not line.startswith("-"):
                continue
            item = line.lstrip("-").strip()
            item = _normalize_display_text(item)
            if item and item not in items:
                items.append(item)
            if len(items) >= max_items:
                return items
    return items


def _summarize_for_log(chunks: list[RagChunk], *, max_chars: int = 500) -> str:
    if not chunks:
        return ""
    parts: list[str] = []
    for chunk in chunks[:3]:
        title = _normalize_display_text((chunk.title or "").strip())
        first = _normalize_display_text(_first_sentence(chunk.body))
        if title and title not in GENERIC_TITLE_TOKENS:
            parts.append(f"{title}: {first}" if first else title)
        elif first:
            parts.append(first)
        if len(" ".join(parts)) >= max_chars:
            break
    text = " ".join(parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def _build_natural_answer(chunks: list[RagChunk]) -> str:
    if not chunks:
        return ""

    subject = _extract_subject(chunks)
    definition = _extract_definition_summary(chunks)
    bullets = _collect_bullets(
        _collect_chunks_by_keywords(chunks, ("体験内容", "特徴")),
        max_items=3,
    )
    activity_chunks = [chunk for chunk in chunks if _chunk_matches(chunk, ("活動カテゴリ", "主催", "運営"))]
    activity_bullets = _collect_bullets(activity_chunks, max_items=2) if activity_chunks else []

    intro = f"ここは、{subject}のサーバーです。" if subject else "ここは、イベントや案内をまとめたサーバーです。"

    second_parts: list[str] = []
    if definition:
        second_parts.append(_normalize_display_text(definition))
    if bullets:
        second_parts.append(" ".join(bullets[:3]))
    if not second_parts:
        second_parts.append("旅行イベントの案内や参加情報を扱っています。")
    second = " ".join(second_parts).strip()
    if second and not second.endswith("。"):
        second += "。"

    paragraphs = [intro, second]
    if activity_bullets:
        third = "主な活動は、" + "、".join(activity_bullets[:3]) + "です。"
        paragraphs.append(third)
    paragraphs = [para for para in paragraphs if para]
    return "\n\n".join(paragraphs[:3])


def summarize_profile_chunks(chunks: list[RagChunk], question: str = "") -> str:
    del question
    return _build_natural_answer(chunks)


def build_channel_profile_preview(
    *,
    root: Path,
    guild_id: int | None = None,
    channel_id: int | None = None,
    scope: str = "auto",
    question: str = "このサーバーはなにするところ？",
    limit: int = 6,
    max_chars: int = 2600,
) -> dict[str, Any]:
    chunks = build_profile_chunks(
        root=root,
        guild_id=guild_id,
        channel_id=channel_id,
        scope=scope,
        limit=limit,
    )
    profile_block = format_profile_chunks(chunks, max_chars=max_chars)
    answer = summarize_profile_chunks(chunks, question=question)
    profile_summary = _summarize_for_log(chunks, max_chars=500)
    return {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "scope": scope,
        "question": question,
        "profile": profile_block,
        "profile_summary": profile_summary,
        "answer": answer,
    }


def build_profile_management_log(preview: dict[str, Any]) -> dict[str, Any]:
    profile = str(preview.get("profile_summary") or preview.get("profile") or "")
    answer = str(preview.get("answer") or "")
    scope = str(preview.get("scope") or "auto")
    guild_id = preview.get("guild_id")
    channel_id = preview.get("channel_id")
    question = str(preview.get("question") or "")
    return {
        "title": "Bot 管理ログ",
        "description": "サーバー・チャンネル・ワールドの説明に応答しました。",
        "level": "info",
        "fields": [
            ("種別", "メンション", True),
            ("カテゴリ", "場所説明", True),
            ("場所", f"guild_id={guild_id} channel_id={channel_id} scope={scope}", False),
            ("質問", question, False),
            ("プロフィール", profile[:800], False),
            ("返信", answer[:800], False),
        ],
    }


def write_jsonl_log(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
