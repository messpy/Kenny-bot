#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.utils.env import load_env_file

load_env_file()

from src.kennybot.utils.db import resolve_database_config
from src.kennybot.utils.message_vector_store import MessageVectorStore
from src.kennybot.utils.paths import MESSAGE_VECTOR_SQLITE_PATH, SERVER_REGISTRY_SQLITE_PATH
from src.kennybot.utils.server_registry import ServerRegistryStore


def _load_sqlite_rows(path: Path, query: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query).fetchall()]


def _json_load(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def migrate_registry(store: ServerRegistryStore) -> dict[str, int]:
    guilds = _load_sqlite_rows(
        SERVER_REGISTRY_SQLITE_PATH,
        "SELECT guild_id, name, settings_json, metadata_json FROM guilds",
    )
    channels = _load_sqlite_rows(
        SERVER_REGISTRY_SQLITE_PATH,
        "SELECT guild_id, channel_id, name, kind, topic, settings_json, metadata_json FROM channels",
    )
    rag_documents = _load_sqlite_rows(
        SERVER_REGISTRY_SQLITE_PATH,
        "SELECT guild_id, channel_id, scope, source_path, doc_type, title, summary, body_text, metadata_json FROM rag_documents",
    )
    message_logs = _load_sqlite_rows(
        SERVER_REGISTRY_SQLITE_PATH,
        "SELECT guild_id, channel_id, message_id, author_id, author, content, timestamp, metadata_json FROM message_logs",
    )

    for row in guilds:
        store.upsert_guild(
            int(row["guild_id"]),
            name=str(row["name"]) if row["name"] is not None else None,
            settings=_json_load(row.get("settings_json")),
            metadata=_json_load(row.get("metadata_json")),
        )
    for row in channels:
        store.upsert_channel(
            int(row["guild_id"]),
            int(row["channel_id"]),
            name=str(row["name"]) if row["name"] is not None else None,
            kind=str(row["kind"]) if row["kind"] is not None else None,
            topic=str(row["topic"]) if row["topic"] is not None else None,
            settings=_json_load(row.get("settings_json")),
            metadata=_json_load(row.get("metadata_json")),
        )
    for row in rag_documents:
        store.upsert_rag_document(
            guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
            channel_id=int(row["channel_id"]) if row["channel_id"] is not None else None,
            scope=str(row["scope"]),
            source_path=str(row["source_path"]),
            doc_type=str(row["doc_type"]),
            title=str(row["title"]),
            summary=str(row.get("summary") or ""),
            body=str(row.get("body_text") or ""),
            metadata=_json_load(row.get("metadata_json")),
        )
    for row in message_logs:
        store.upsert_message_log(
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            message_id=int(row["message_id"]),
            author_id=int(row["author_id"]),
            author=str(row["author"]),
            content=str(row["content"]),
            timestamp=str(row["timestamp"]),
            metadata=_json_load(row.get("metadata_json")),
        )
    return {
        "guilds": len(guilds),
        "channels": len(channels),
        "rag_documents": len(rag_documents),
        "message_logs": len(message_logs),
    }


def migrate_embeddings(store: MessageVectorStore) -> int:
    rows = _load_sqlite_rows(
        MESSAGE_VECTOR_SQLITE_PATH,
        "SELECT guild_id, channel_id, message_id, author_id, author, content, timestamp, embedding_json FROM message_embeddings",
    )
    payload: list[dict[str, Any]] = []
    for row in rows:
        embedding = None
        if row.get("embedding_json"):
            try:
                decoded = json.loads(str(row["embedding_json"]))
                if isinstance(decoded, list):
                    embedding = decoded
            except Exception:
                embedding = None
        payload.append(
            {
                "guild_id": int(row["guild_id"]),
                "channel_id": int(row["channel_id"]),
                "message_id": int(row["message_id"]),
                "author_id": int(row["author_id"]),
                "author": str(row["author"]),
                "content": str(row["content"]),
                "timestamp": str(row["timestamp"]),
                "embedding": embedding,
            }
        )
    store.upsert_messages(payload)
    return len(payload)


def main() -> int:
    db = resolve_database_config()
    if db.backend != "mariadb":
        print("KENNYBOT_DB_BACKEND=mariadb を設定してから実行してください", file=sys.stderr)
        return 1

    registry = ServerRegistryStore()
    vectors = MessageVectorStore()
    registry_counts = migrate_registry(registry)
    embedding_count = migrate_embeddings(vectors)
    print(
        json.dumps(
            {
                "ok": True,
                "backend": db.backend,
                "registry": registry_counts,
                "message_embeddings": embedding_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
