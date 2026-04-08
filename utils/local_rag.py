from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from utils.command_catalog import COMMAND_CATEGORY_ORDER, HELP_SECTIONS, SLASH_COMMANDS


@dataclass
class RagChunk:
    source: str
    title: str
    body: str


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    parts = re.split(r"[\s\r\n\t:：、。・,./()（）\[\]{}!?！？]+", text)
    return [p for p in parts if p]


def _split_markdown_sections(text: str) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    cur_title = "README"
    cur_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if cur_lines:
                body = "\n".join(cur_lines).strip()
                if body:
                    chunks.append(RagChunk(source="README", title=cur_title, body=body))
            cur_title = line.lstrip("#").strip() or "README"
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        body = "\n".join(cur_lines).strip()
        if body:
            chunks.append(RagChunk(source="README", title=cur_title, body=body))
    return chunks


def _chunks_from_mapping(source: str, obj: object) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            title = str(key).strip() or source
            if isinstance(value, dict):
                body = "\n".join(f"{k}: {v}" for k, v in value.items()).strip()
            elif isinstance(value, list):
                body = "\n".join(str(item) for item in value).strip()
            else:
                body = str(value).strip()
            if body:
                chunks.append(RagChunk(source=source, title=title, body=body))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj, start=1):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or f"{source} {idx}").strip()
                body = str(item.get("body") or item.get("content") or "").strip()
                if not body:
                    extra = {k: v for k, v in item.items() if k not in {"title", "name", "body", "content"}}
                    body = "\n".join(f"{k}: {v}" for k, v in extra.items()).strip()
                if body:
                    chunks.append(RagChunk(source=source, title=title, body=body))
            else:
                body = str(item).strip()
                if body:
                    chunks.append(RagChunk(source=source, title=f"{source} {idx}", body=body))
    return chunks


def _load_extra_rag_file(path: Path) -> list[RagChunk]:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _split_markdown_sections(path.read_text(encoding="utf-8", errors="ignore"))
    if suffix == ".json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        return _chunks_from_mapping(path.stem, obj)
    if suffix == ".toml":
        obj = tomllib.loads(path.read_text(encoding="utf-8"))
        return _chunks_from_mapping(path.stem, obj)
    return []


def _static_chunks() -> list[RagChunk]:
    chunks = [
        RagChunk(
            source="BOT",
            title="会話",
            body=(
                "Bot はメンションや Bot への返信で AI 応答できます。"
                "DM でもそのまま AI 会話できます。"
                "会話時は本人履歴、チャンネル履歴、意味的に近い過去発言を状況に応じて使い分けます。"
                "README と data/chat_rag.md/json/toml のローカル知識も参照できます。"
                "最新情報が必要で web search が使える構成なら、必要時だけ web search/web fetch を使います。"
            ),
        ),
        RagChunk(
            source="BOT",
            title="音声",
            body=(
                "VOICEVOX 読み上げは /tts_join で開始します。"
                "コマンドを実行した人がいる通話に参加し、そのチャンネルを読み上げ対象にします。"
                "議事録は /minutes_start で開始し、同じチャンネルに文字起こしや結果を返します。"
            ),
        ),
        RagChunk(
            source="BOT",
            title="ゲーム",
            body=(
                "人狼役職配布は /game mode:人狼役職配布 です。"
                "人狼だけが DM のリアクションで襲撃対象を選びます。"
                "あいうえおバトルは /game mode:あいうえおバトル で、1人から開始できます。"
                "お題は DM で送信し、ひらがなのみ7文字以下、小文字や濁点や半濁点やーも使えます。"
            ),
        ),
    ]

    for section in HELP_SECTIONS:
        chunks.append(
            RagChunk(
                source="HELP",
                title=section.title,
                body="\n".join(section.lines),
            )
        )

    commands_by_category: dict[str, list[str]] = {category: [] for category in COMMAND_CATEGORY_ORDER}
    for meta in SLASH_COMMANDS.values():
        commands_by_category.setdefault(meta.category, []).append(f"/{meta.name}: {meta.description}")

    for category in COMMAND_CATEGORY_ORDER:
        lines = commands_by_category.get(category, [])
        if not lines:
            continue
        chunks.append(
            RagChunk(
                source="HELP",
                title=f"コマンド {category}",
                body="\n".join(lines),
            )
        )

    return chunks


class LocalRAG:
    def __init__(self, root: Path):
        self.root = root
        self._extra_paths = [
            self.root / "data" / "chat_rag.md",
            self.root / "data" / "chat_rag.json",
            self.root / "data" / "chat_rag.toml",
        ]

    def _load_chunks(self) -> list[RagChunk]:
        chunks = _static_chunks()
        readme = self.root / "README.md"
        if readme.exists():
            try:
                chunks.extend(_split_markdown_sections(readme.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                pass
        for path in self._extra_paths:
            if not path.exists():
                continue
            try:
                extra_chunks = _load_extra_rag_file(path)
                for chunk in extra_chunks:
                    chunks.append(RagChunk(source=f"RAG:{path.name}", title=chunk.title, body=chunk.body))
            except Exception:
                pass
        return chunks

    def retrieve(self, query: str, limit: int = 4) -> list[RagChunk]:
        tokens = set(_tokenize(query))
        if not tokens:
            return self._load_chunks()[:limit]

        scored: list[tuple[int, RagChunk]] = []
        for chunk in self._load_chunks():
            hay = f"{chunk.title}\n{chunk.body}".lower()
            score = 0
            for token in tokens:
                if token in hay:
                    score += 2
            if query.lower() in hay:
                score += 4
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [chunk for _, chunk in scored[:limit]]
        if top:
            return top
        return self._load_chunks()[:limit]
