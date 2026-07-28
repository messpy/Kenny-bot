from __future__ import annotations

import json
import re
import tomllib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from src.kennybot.utils.command_catalog import COMMAND_CATEGORY_ORDER, HELP_SECTIONS, SLASH_COMMANDS
from src.kennybot.utils.paths import KNOWLEDGE_DIR, LEGACY_KNOWLEDGE_DIR, SERVER_DIR, SERVER_REGISTRY_SQLITE_PATH
from src.kennybot.storage.server_repository import ServerRegistryStore, create_server_registry, get_server_registry


@dataclass
class RagChunk:
    source: str
    title: str
    body: str


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    parts = re.split(r"[\s\r\n\t:：、。・,./()（）\[\]{}!?！？]+", text)
    return [p for p in parts if p]


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


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


def load_rag_chunks_from_directory(directory: Path) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    if not directory.exists():
        return chunks

    extra_names = (
        "faq.json",
        "faq.md",
        "chat_rag.md",
        "chat_rag.json",
        "chat_rag.toml",
        "rules.md",
        "rules.json",
        "rules.toml",
    )
    for name in extra_names:
        path = directory / name
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


def _registry_store_for_root(root: Path) -> ServerRegistryStore:
    return create_server_registry(root)


def _registry_marker_path(root: Path, registry: ServerRegistryStore) -> Path:
    if registry._db.backend == "sqlite":
        return root / SERVER_REGISTRY_SQLITE_PATH
    return root / SERVER_DIR / "registry.mariadb"


def load_rag_chunks_from_registry(
    registry: ServerRegistryStore,
    *,
    guild_id: int | None = None,
    channel_id: int | None = None,
    scope: str = "auto",
    limit: int = 50,
) -> list[RagChunk]:
    normalized_scope = (scope or "auto").strip().lower()
    rows: list[dict[str, object]] = []
    seen: set[tuple[object, object, object]] = set()

    def add_documents(*, doc_scope: str, guild: int | None, channel: int | None) -> None:
        if guild is None and doc_scope in {"guild", "channel"}:
            return
        if doc_scope == "channel" and channel is None:
            return
        for row in registry.list_rag_documents(
            guild_id=guild,
            channel_id=channel,
            scope=doc_scope,
            limit=limit,
        ):
            key = (row.get("scope"), row.get("source_path"), row.get("title"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    if normalized_scope == "guild":
        add_documents(doc_scope="guild", guild=guild_id, channel=None)
    elif normalized_scope == "channel":
        add_documents(doc_scope="channel", guild=guild_id, channel=channel_id)
    else:
        add_documents(doc_scope="channel", guild=guild_id, channel=channel_id)
        add_documents(doc_scope="guild", guild=guild_id, channel=None)

    chunks: list[RagChunk] = []
    for row in rows:
        body = str(row.get("body") or row.get("summary") or "").strip()
        if not body:
            continue
        source_name = Path(str(row.get("source_path") or "")).name or "registry"
        chunks.append(
            RagChunk(
                source=f"RAG:{source_name}",
                title=str(row.get("title") or source_name),
                body=body,
            )
        )
    return chunks[: max(1, int(limit or 50))]


def _static_chunks() -> list[RagChunk]:
    chunks = [
        RagChunk(
            source="BOT",
            title="会話",
            body=(
                "Bot はメンションや Bot への返信で会話応答できます。"
                "DM でもそのまま会話できます。"
                "会話時は本人履歴、チャンネル履歴、意味的に近い過去発言を状況に応じて使い分けます。"
                "data/knowledge/chat_rag.md/json/toml のローカル知識も参照できます。"
                "曖昧な質問や裏取りが必要な質問では、web search/web fetch で確認してから答えることがあります。"
            ),
        ),
        RagChunk(
            source="BOT",
            title="音声",
            body=(
                "VOICEVOX 読み上げは通話チャンネルに参加して開始します。"
                "コマンドを実行した人がいる通話に参加し、そのチャンネルを読み上げ対象にします。"
                "議事録はVCで開始し、同じチャンネルに文字起こしや結果を返します。"
            ),
        ),
        RagChunk(
            source="BOT",
            title="ゲーム",
            body=(
                "人狼役職配布はゲーム機能から開始できます。"
                "人狼には霊媒師も含まれ、夜行動と昼投票は DM のリアクションで進みます。"
                "騎士は同じ相手を連続で護衛できません。"
                "あいうえおバトルは1人から開始できます。"
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
        legacy_knowledge_root = self.root / LEGACY_KNOWLEDGE_DIR
        legacy_root = self.root / "data"
        paths: list[Path] = []
        for name in ("chat_rag.md", "chat_rag.json", "chat_rag.toml"):
            knowledge_path = knowledge_root / name
            legacy_knowledge_path = legacy_knowledge_root / name
            legacy_path = legacy_root / name
            if knowledge_path.exists():
                paths.append(knowledge_path)
            elif legacy_knowledge_path.exists():
                paths.append(legacy_knowledge_path)
            elif legacy_path.exists():
                paths.append(legacy_path)
            else:
                paths.append(knowledge_path)
        return paths

    def _channel_extra_paths(self, guild_id: int | None, channel_id: int | None) -> list[Path]:
        del guild_id, channel_id
        return []

    def _scoped_registry_chunks(
        self,
        *,
        guild_id: int | None,
        channel_id: int | None,
        channel_only: bool,
        limit: int = 24,
    ) -> list[RagChunk]:
        try:
            registry = _registry_store_for_root(self.root)
            scope = "channel" if channel_only else "auto"
            return load_rag_chunks_from_registry(
                registry,
                guild_id=guild_id,
                channel_id=channel_id,
                scope=scope,
                limit=limit,
            )
        except Exception:
            return []

    def _load_chunks(
        self,
        *,
        capability_only: bool = False,
        guild_id: int | None = None,
        channel_id: int | None = None,
        channel_only: bool = False,
    ) -> list[RagChunk]:
        chunks = [] if channel_only else _static_chunks()
        scoped_registry_chunks = self._scoped_registry_chunks(
            guild_id=guild_id,
            channel_id=channel_id,
            channel_only=channel_only,
        )
        if scoped_registry_chunks:
            chunks.extend(scoped_registry_chunks)
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
        guild_id: int | None = None,
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

        if guild_id is not None:
            source_path = f"rag://guild/{int(guild_id)}/channel/{int(channel_id)}/faq/{_sha256_text(question + chr(10) + answer)}"
        else:
            source_path = f"rag://channel/{int(channel_id)}/faq/{_sha256_text(question + chr(10) + answer)}"

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
        registry = get_server_registry()
        try:
            registry.upsert_rag_document(
                scope="channel",
                guild_id=guild_id,
                channel_id=channel_id,
                source_path=source_path,
                doc_type="faq.json",
                title=question,
                summary=answer[:500],
                body=answer,
                metadata={
                    "tags": cleaned_tags,
                    "question": question,
                    **(metadata or {}),
                },
            )
        except Exception:
            pass
        return _registry_marker_path(self.root, registry)

    def append_guild_qa(self, **kwargs: object) -> Path:
        guild_id = kwargs.pop("guild_id", None)
        if guild_id is None:
            guild_id = kwargs.pop("channel_id", None)
        if guild_id is None:
            raise TypeError("guild_id is required")
        question = str(kwargs.pop("question", "")).strip()
        answer = str(kwargs.pop("answer", "")).strip()
        if not question:
            raise ValueError("question is required")
        if not answer:
            raise ValueError("answer is required")
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
        source_path = f"rag://guild/{int(guild_id)}/faq/{_sha256_text(question + chr(10) + answer)}"
        registry = get_server_registry()
        try:
            registry.upsert_rag_document(
                scope="guild",
                guild_id=int(guild_id),
                source_path=source_path,
                doc_type="faq.json",
                title=question,
                summary=answer[:500],
                body=answer,
                metadata={
                    "tags": cleaned_tags,
                    "question": question,
                    "guild_id": int(guild_id),
                },
            )
        except Exception:
            pass
        return _registry_marker_path(self.root, registry)
