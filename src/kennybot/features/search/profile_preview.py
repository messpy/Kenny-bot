from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any

from src.kennybot.features.search.local_rag import RagChunk, load_rag_chunks_from_directory, load_rag_chunks_from_registry
from src.kennybot.utils.paths import CHANNEL_RAG_DIR, SERVER_DIR, SERVER_RAG_DIR
from src.kennybot.storage.server_repository import create_server_registry


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


GENERIC_TITLE_TOKENS = {"README", "定義", "概要", "紹介", "説明"}
SECTION_PRIORITY = ("定義", "体験内容", "主催", "運営", "活動カテゴリ")
DISPLAY_REPLACEMENTS = (("出典", "出展"),)
OPERATIONAL_TITLE_TOKENS = (
    "運用方針",
    "メモ",
    "向けメモ",
    "管理メモ",
    "内部メモ",
)
QUESTION_STOPWORDS = (
    "この",
    "その",
    "どの",
    "なに",
    "何",
    "ところ",
    "チャンネル",
    "サーバー",
    "何をする",
    "何する",
    "ですか",
    "とは",
)


def build_profile_chunks(
    *,
    root: Path,
    guild_id: int | None = None,
    channel_id: int | None = None,
    scope: str = "auto",
    limit: int = 6,
) -> list[RagChunk]:
    normalized_limit = max(1, min(int(limit or 6), 6))
    registry = create_server_registry(root)
    registry_chunks = load_rag_chunks_from_registry(
        registry,
        guild_id=guild_id,
        channel_id=channel_id,
        scope=scope,
        limit=normalized_limit,
    )
    if registry_chunks:
        return registry_chunks[:normalized_limit]
    allow_file_fallback = os.environ.get("KENNYBOT_ALLOW_FILE_RAG_FALLBACK", "").strip().lower() in {"1", "true", "yes"}
    if not allow_file_fallback:
        return []
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


def is_user_facing_profile_chunk(chunk: RagChunk) -> bool:
    title = (chunk.title or "").strip()
    if not title:
        return True
    return not any(token in title for token in OPERATIONAL_TITLE_TOKENS)


def select_display_profile_chunks(chunks: list[RagChunk]) -> list[RagChunk]:
    display_chunks = [chunk for chunk in chunks if is_user_facing_profile_chunk(chunk)]
    return display_chunks or chunks


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


def _question_keywords(question: str) -> list[str]:
    text = re.sub(r"[？?！!。、「」『』（）()\[\]`]", " ", str(question or ""))
    raw_tokens = [token.strip() for token in re.split(r"[\s,./]+", text) if token.strip()]
    keywords: list[str] = []
    for token in raw_tokens:
        normalized = token.strip()
        if len(normalized) <= 1:
            continue
        if any(stop in normalized for stop in QUESTION_STOPWORDS):
            continue
        if normalized not in keywords:
            keywords.append(normalized)
    return keywords


def _question_target_chunks(chunks: list[RagChunk], question: str) -> list[RagChunk]:
    question_text = str(question or "")
    direct_title_matches = [
        chunk
        for chunk in chunks
        if (chunk.title or "").strip() and (chunk.title or "").strip() in question_text
    ]
    if direct_title_matches:
        return direct_title_matches

    keywords = _question_keywords(question)
    if not keywords:
        return []
    matched: list[RagChunk] = []
    for chunk in chunks:
        title = (chunk.title or "").strip()
        body = chunk.body or ""
        if any(keyword == title or keyword in title or keyword in body for keyword in keywords):
            matched.append(chunk)
    return matched


def _build_natural_answer(chunks: list[RagChunk], question: str = "") -> str:
    if not chunks:
        return ""

    priority_chunks: list[RagChunk] = []
    target_chunks = _question_target_chunks(chunks, question)
    if target_chunks:
        priority_chunks.extend(target_chunks)
        priority_chunks.extend(
            chunk
            for chunk in chunks
            if chunk not in priority_chunks and _chunk_matches(chunk, ("サーバー全体", "概要", "説明"))
        )
    else:
        priority_chunks.extend(_collect_chunks_by_keywords(chunks, ("定義", "概要", "紹介", "説明")))
        priority_chunks.extend(_collect_chunks_by_keywords(chunks, ("体験内容", "特徴")))
        priority_chunks.extend(
            chunk for chunk in chunks if _chunk_matches(chunk, ("活動カテゴリ", "主催", "運営"))
        )
    if not priority_chunks:
        priority_chunks = chunks[:3]

    sentences: list[str] = []
    for chunk in priority_chunks:
        first = _normalize_display_text(_first_sentence(chunk.body))
        if not first:
            continue
        if first not in sentences:
            sentences.append(first)
        if len(sentences) >= 3:
            break

    if not sentences:
        return ""
    return "\n\n".join(
        sentence if sentence.endswith("。") else f"{sentence}。"
        for sentence in sentences[:3]
    )


def summarize_profile_chunks(chunks: list[RagChunk], question: str = "") -> str:
    return _build_natural_answer(chunks, question=question)


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
    display_chunks = select_display_profile_chunks(chunks)
    profile_block = format_profile_chunks(display_chunks, max_chars=max_chars)
    answer = summarize_profile_chunks(display_chunks, question=question)
    profile_summary = _summarize_for_log(display_chunks, max_chars=500)
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
