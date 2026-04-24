from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.kennybot.utils.local_rag import RagChunk, load_rag_chunks_from_directory
from src.kennybot.utils.paths import CHANNEL_RAG_DIR


GENERIC_TITLE_TOKENS = {"README", "定義", "概要", "紹介", "説明"}


def _scoped_directories(
    *,
    root: Path,
    guild_id: int | None,
    channel_id: int | None,
    scope: str,
) -> list[Path]:
    channel_root = None
    if guild_id is not None and channel_id is not None:
        channel_root = root / CHANNEL_RAG_DIR / str(int(guild_id)) / "channels" / str(int(channel_id))
    elif channel_id is not None:
        channel_root = root / CHANNEL_RAG_DIR / str(int(channel_id))

    if guild_id is not None:
        guild_root = root / CHANNEL_RAG_DIR / str(int(guild_id))
    else:
        guild_root = None

    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope == "guild":
        return [guild_root] if guild_root is not None else []
    if normalized_scope == "channel":
        return [channel_root] if channel_root is not None else []
    if normalized_scope == "legacy_channel":
        if channel_id is None:
            return []
        return [root / CHANNEL_RAG_DIR / str(int(channel_id))]

    out: list[Path] = []
    if channel_root is not None:
        out.append(channel_root)
    if guild_root is not None:
        out.append(guild_root)
    if channel_id is not None:
        legacy_root = root / CHANNEL_RAG_DIR / str(int(channel_id))
        if legacy_root not in out:
            out.append(legacy_root)
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


def summarize_profile_chunks(chunks: list[RagChunk], question: str = "") -> str:
    if not chunks:
        return ""

    primary = chunks[0]
    headline = _first_sentence(primary.body)
    bullet_lines: list[str] = []
    for chunk in chunks:
        for raw_line in chunk.body.splitlines():
            line = raw_line.strip()
            if not line.startswith("-"):
                continue
            item = line.lstrip("-").strip()
            if item and item not in bullet_lines:
                bullet_lines.append(item)
            if len(bullet_lines) >= 3:
                break
        if len(bullet_lines) >= 3:
            break

    lines: list[str] = []
    if primary.title and primary.title not in GENERIC_TITLE_TOKENS:
        lines.append(primary.title)
    if question:
        if headline:
            lines.append(f"{question} に対しては、{headline}")
        else:
            lines.append(f"{question} に対しては、情報を確認しました。")
    elif headline:
        lines.append(headline)

    for item in bullet_lines[:3]:
        lines.append(f"- {item}")
    return "\n".join(lines)


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
    return {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "scope": scope,
        "question": question,
        "profile": profile_block,
        "answer": answer,
    }


def build_profile_management_log(preview: dict[str, Any]) -> dict[str, Any]:
    profile = str(preview.get("profile") or "")
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
