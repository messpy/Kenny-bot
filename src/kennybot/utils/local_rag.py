from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from src.kennybot.utils.command_catalog import COMMAND_CATEGORY_ORDER, HELP_SECTIONS, SLASH_COMMANDS
from src.kennybot.utils.paths import CHANNEL_RAG_DIR, KNOWLEDGE_DIR
from src.kennybot.utils.scoped_data import channel_scope_dir, guild_scope_dir


@dataclass
class RagChunk:
    source: str
    title: str
    body: str


README_CAPABILITY_TITLE_TOKENS = (
    "主な機能",
    "設定方法",
    "使用方法",
    "会話履歴",
    "semantic memory",
    "トラブルシューティング",
)


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


def _should_skip_chunk(chunk: RagChunk) -> bool:
    title = (chunk.title or "").strip().lower()
    if title in {"サンプル文", "sample文", "sample text", "sample"}:
        return True
    return False


def _filter_capability_chunks(chunks: list[RagChunk]) -> list[RagChunk]:
    filtered: list[RagChunk] = []
    for chunk in chunks:
        title = (chunk.title or "").strip().lower()
        if any(token.lower() in title for token in README_CAPABILITY_TITLE_TOKENS):
            filtered.append(chunk)
    return filtered


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
                question = str(item.get("question") or item.get("title") or item.get("name") or "").strip()
                answer = str(item.get("answer") or item.get("body") or item.get("content") or "").strip()
                title = question or f"{source} {idx}"
                body_lines: list[str] = []
                if question:
                    body_lines.append(f"Q: {question}")
                if answer:
                    body_lines.append(f"A: {answer}")
                tags = item.get("tags")
                if isinstance(tags, list) and tags:
                    tag_text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
                    if tag_text:
                        body_lines.append(f"Tags: {tag_text}")
                extra = {
                    k: v
                    for k, v in item.items()
                    if k not in {"title", "name", "question", "answer", "body", "content", "tags"}
                }
                if extra:
                    body_lines.extend(f"{k}: {v}" for k, v in extra.items())
                body = "\n".join(line for line in body_lines if line).strip()
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
                "Bot はメンションや Bot への返信で会話応答できます。"
                "DM でもそのまま会話できます。"
                "会話時は本人履歴、チャンネル履歴、意味的に近い過去発言を状況に応じて使い分けます。"
                "README と knowledge/chat_rag.md/json/toml のローカル知識も参照できます。"
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
                "人狼には霊媒師も含まれ、夜行動と昼投票は DM のリアクションで進みます。"
                "騎士は同じ相手を連続で護衛できません。"
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
        self._global_extra_paths = self._resolve_extra_paths()

    def _resolve_extra_paths(self) -> list[Path]:
        knowledge_root = self.root / KNOWLEDGE_DIR
        legacy_root = self.root / "data"
        paths: list[Path] = []
        for name in ("chat_rag.md", "chat_rag.json", "chat_rag.toml"):
            knowledge_path = knowledge_root / name
            legacy_path = legacy_root / name
            if knowledge_path.exists():
                paths.append(knowledge_path)
            elif legacy_path.exists():
                paths.append(legacy_path)
            else:
                paths.append(knowledge_path)
        return paths

    def _channel_extra_paths(self, guild_id: int | None, channel_id: int | None) -> list[Path]:
        if not guild_id and not channel_id:
            return []
        paths: list[Path] = []
        extra_names = (
            "faq.json",
            "faq.md",
            "chat_rag.md",
            "chat_rag.json",
            "chat_rag.toml",
            "rules.md",
            "rules.json",
            "rules.toml",
            "settings.yaml",
            "settings.json",
            "settings.toml",
        )
        if guild_id:
            guild_root = guild_scope_dir(guild_id)
            for name in extra_names:
                path = guild_root / name
                if path.exists():
                    paths.append(path)
        if guild_id and channel_id:
            channel_root = channel_scope_dir(guild_id, channel_id)
            for name in extra_names:
                path = channel_root / name
                if path.exists():
                    paths.append(path)
        if channel_id:
            channel_root = self.root / CHANNEL_RAG_DIR / str(channel_id)
            for name in extra_names:
                path = channel_root / name
                if path.exists():
                    paths.append(path)
        return paths

    def _load_chunks(
        self,
        *,
        capability_only: bool = False,
        guild_id: int | None = None,
        channel_id: int | None = None,
        channel_only: bool = False,
    ) -> list[RagChunk]:
        chunks = [] if channel_only else _static_chunks()
        if not channel_only:
            readme = self.root / "README.md"
            if readme.exists():
                try:
                    readme_chunks = _split_markdown_sections(readme.read_text(encoding="utf-8", errors="ignore"))
                    if capability_only:
                        readme_chunks = _filter_capability_chunks(readme_chunks)
                    chunks.extend(readme_chunks)
                except Exception:
                    pass
        for path in self._channel_extra_paths(guild_id, channel_id):
            if not path.exists():
                continue
                try:
                    extra_chunks = _load_extra_rag_file(path)
                    for chunk in extra_chunks:
                        if _should_skip_chunk(chunk):
                            continue
                        chunks.append(RagChunk(source=f"RAG:{path.name}", title=chunk.title, body=chunk.body))
                except Exception:
                    pass
        if not channel_only:
            for path in self._global_extra_paths:
                if not path.exists():
                    continue
                try:
                    extra_chunks = _load_extra_rag_file(path)
                    for chunk in extra_chunks:
                        if _should_skip_chunk(chunk):
                            continue
                        chunks.append(RagChunk(source=f"RAG:{path.name}", title=chunk.title, body=chunk.body))
                except Exception:
                    pass
        return chunks

    def retrieve(
        self,
        query: str,
        limit: int = 4,
        *,
        capability_only: bool = False,
        guild_id: int | None = None,
        channel_id: int | None = None,
        channel_only: bool = False,
    ) -> list[RagChunk]:
        tokens = set(_tokenize(query))
        chunks = self._load_chunks(
            capability_only=capability_only,
            guild_id=guild_id,
            channel_id=channel_id,
            channel_only=channel_only,
        )
        if not tokens:
            return chunks[:limit]

        scored: list[tuple[int, RagChunk]] = []
        for chunk in chunks:
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
        return chunks[:limit]

    def append_channel_qa(
        self,
        *,
        channel_id: int,
        question: str,
        answer: str,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Path:
        question = question.strip()
        answer = answer.strip()
        if not question:
            raise ValueError("question is required")
        if not answer:
            raise ValueError("answer is required")

        channel_root = self.root / CHANNEL_RAG_DIR / str(channel_id)
        channel_root.mkdir(parents=True, exist_ok=True)
        faq_path = channel_root / "faq.json"
        entries: list[dict[str, object]] = []
        if faq_path.exists():
            try:
                loaded = json.loads(faq_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    entries = [item for item in loaded if isinstance(item, dict)]
            except Exception:
                entries = []

        entry: dict[str, object] = {
            "title": question,
            "question": question,
            "answer": answer,
        }
        cleaned_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if cleaned_tags:
            entry["tags"] = cleaned_tags
        if metadata:
            for key, value in metadata.items():
                if value is None:
                    continue
                entry[key] = value
        entries.append(entry)
        faq_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return faq_path

    def append_guild_qa(self, **kwargs: object) -> Path:
        guild_id = kwargs.pop("guild_id", None)
        if guild_id is None:
            guild_id = kwargs.pop("channel_id", None)
        if guild_id is None:
            raise TypeError("guild_id is required")
        guild_root = guild_scope_dir(int(guild_id))
        guild_root.mkdir(parents=True, exist_ok=True)
        question = str(kwargs.pop("question", "")).strip()
        answer = str(kwargs.pop("answer", "")).strip()
        if not question:
            raise ValueError("question is required")
        if not answer:
            raise ValueError("answer is required")
        faq_path = guild_root / "faq.json"
        entries: list[dict[str, object]] = []
        if faq_path.exists():
            try:
                loaded = json.loads(faq_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    entries = [item for item in loaded if isinstance(item, dict)]
            except Exception:
                entries = []
        entry: dict[str, object] = {
            "title": question,
            "question": question,
            "answer": answer,
        }
        tags = kwargs.pop("tags", None)
        cleaned_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if cleaned_tags:
            entry["tags"] = cleaned_tags
        metadata = kwargs.pop("metadata", None)
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if value is None:
                    continue
                entry[key] = value
        entries.append(entry)
        faq_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return faq_path
