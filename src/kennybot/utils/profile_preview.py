from __future__ import annotations

from pathlib import Path
from typing import Any

from src.kennybot.utils.local_rag import LocalRAG, RagChunk


GENERIC_TITLE_TOKENS = {"README", "定義", "概要", "紹介", "説明"}


def profile_candidate_ids(guild_id: int | None = None, channel_id: int | None = None) -> list[int]:
    if guild_id:
        return [int(guild_id)]
    if channel_id:
        return [int(channel_id)]
    return []


def build_profile_chunks(
    *,
    root: Path,
    guild_id: int | None = None,
    channel_id: int | None = None,
    limit: int = 6,
) -> list[RagChunk]:
    rag = LocalRAG(root)
    normalized_limit = max(1, min(int(limit or 6), 6))
    for candidate_id in profile_candidate_ids(guild_id, channel_id):
        chunks = rag.retrieve(
            "",
            limit=normalized_limit,
            guild_id=guild_id,
            channel_id=candidate_id,
            channel_only=True,
        )
        if chunks:
            return chunks
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
    question: str = "このサーバーはなにするところ？",
    limit: int = 6,
    max_chars: int = 2600,
) -> dict[str, Any]:
    chunks = build_profile_chunks(root=root, guild_id=guild_id, channel_id=channel_id, limit=limit)
    profile_block = format_profile_chunks(chunks, max_chars=max_chars)
    answer = summarize_profile_chunks(chunks, question=question)
    return {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "question": question,
        "profile": profile_block,
        "answer": answer,
    }
