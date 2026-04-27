from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.kennybot.utils.paths import SERVER_DB_PATH


UTC = timezone.utc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


class ServerRegistryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guilds (
                    guild_id INTEGER PRIMARY KEY,
                    name TEXT,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    name TEXT,
                    kind TEXT,
                    topic TEXT,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    scope TEXT NOT NULL,
                    source_path TEXT NOT NULL UNIQUE,
                    doc_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    body_hash TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_documents_scope "
                "ON rag_documents (guild_id, channel_id, scope, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_channels_guild "
                "ON channels (guild_id, updated_at DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_logs (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL PRIMARY KEY,
                    author_id INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_logs_channel_time "
                "ON message_logs (guild_id, channel_id, timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_logs_author_time "
                "ON message_logs (guild_id, channel_id, author_id, timestamp DESC)"
            )
            conn.commit()

    def upsert_guild(
        self,
        guild_id: int,
        *,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        settings_json = _json_dump(settings or {})
        metadata_json = _json_dump(metadata or {})
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT created_at FROM guilds WHERE guild_id = ?",
                (int(guild_id),),
            ).fetchone()
            created_at = str(row["created_at"]) if row else now
            conn.execute(
                """
                INSERT INTO guilds (
                    guild_id, name, settings_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    name=COALESCE(excluded.name, guilds.name),
                    settings_json=excluded.settings_json,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (int(guild_id), name, settings_json, metadata_json, created_at, now),
            )
            conn.commit()

    def upsert_channel(
        self,
        guild_id: int,
        channel_id: int,
        *,
        name: str | None = None,
        kind: str | None = None,
        topic: str | None = None,
        settings: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        settings_json = _json_dump(settings or {})
        metadata_json = _json_dump(metadata or {})
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT created_at FROM channels WHERE guild_id = ? AND channel_id = ?",
                (int(guild_id), int(channel_id)),
            ).fetchone()
            created_at = str(row["created_at"]) if row else now
            conn.execute(
                """
                INSERT INTO channels (
                    guild_id, channel_id, name, kind, topic, settings_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                    name=COALESCE(excluded.name, channels.name),
                    kind=COALESCE(excluded.kind, channels.kind),
                    topic=COALESCE(excluded.topic, channels.topic),
                    settings_json=excluded.settings_json,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    int(guild_id),
                    int(channel_id),
                    name,
                    kind,
                    topic,
                    settings_json,
                    metadata_json,
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def upsert_rag_document(
        self,
        *,
        scope: str,
        source_path: str | Path,
        title: str,
        summary: str = "",
        guild_id: int | None = None,
        channel_id: int | None = None,
        doc_type: str = "markdown",
        body: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        metadata_json = _json_dump(metadata or {})
        body_hash = _sha256_text(body or "")
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT created_at FROM rag_documents WHERE source_path = ?",
                (str(source_path),),
            ).fetchone()
            created_at = str(row["created_at"]) if row else now
            conn.execute(
                """
                INSERT INTO rag_documents (
                    guild_id, channel_id, scope, source_path, doc_type, title, summary,
                    body_hash, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_path) DO UPDATE SET
                    guild_id=excluded.guild_id,
                    channel_id=excluded.channel_id,
                    scope=excluded.scope,
                    doc_type=excluded.doc_type,
                    title=excluded.title,
                    summary=excluded.summary,
                    body_hash=excluded.body_hash,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    guild_id,
                    channel_id,
                    scope,
                    str(source_path),
                    doc_type,
                    title,
                    summary,
                    body_hash,
                    metadata_json,
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def get_guild(self, guild_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM guilds WHERE guild_id = ?",
                (int(guild_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "guild_id": int(row["guild_id"]),
            "name": row["name"],
            "settings": json.loads(row["settings_json"] or "{}"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_channel(self, guild_id: int, channel_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE guild_id = ? AND channel_id = ?",
                (int(guild_id), int(channel_id)),
            ).fetchone()
        if row is None:
            return None
        return {
            "guild_id": int(row["guild_id"]),
            "channel_id": int(row["channel_id"]),
            "name": row["name"],
            "kind": row["kind"],
            "topic": row["topic"],
            "settings": json.loads(row["settings_json"] or "{}"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_rag_documents(
        self,
        *,
        guild_id: int | None = None,
        channel_id: int | None = None,
        scope: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["1 = 1"]
        params: list[Any] = []
        if guild_id is not None:
            clauses.append("guild_id = ?")
            params.append(int(guild_id))
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(int(channel_id))
        if scope is not None:
            clauses.append("scope = ?")
            params.append(str(scope))
        params.append(max(1, int(limit)))
        sql = (
            "SELECT guild_id, channel_id, scope, source_path, doc_type, title, summary, body_hash, "
            "metadata_json, created_at, updated_at "
            "FROM rag_documents WHERE "
            f"{' AND '.join(clauses)} "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "guild_id": row["guild_id"],
                "channel_id": row["channel_id"],
                "scope": row["scope"],
                "source_path": row["source_path"],
                "doc_type": row["doc_type"],
                "title": row["title"],
                "summary": row["summary"],
                "body_hash": row["body_hash"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def upsert_message_log(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        author_id: int,
        author: str,
        content: str,
        timestamp: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        metadata_json = _json_dump(metadata or {})
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT created_at FROM message_logs WHERE message_id = ?",
                (int(message_id),),
            ).fetchone()
            created_at = str(row["created_at"]) if row else now
            conn.execute(
                """
                INSERT INTO message_logs (
                    guild_id, channel_id, message_id, author_id, author, content, timestamp,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    guild_id=excluded.guild_id,
                    channel_id=excluded.channel_id,
                    author_id=excluded.author_id,
                    author=excluded.author,
                    content=excluded.content,
                    timestamp=excluded.timestamp,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    int(guild_id),
                    int(channel_id),
                    int(message_id),
                    int(author_id),
                    author,
                    content,
                    timestamp,
                    metadata_json,
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def list_message_logs(
        self,
        *,
        guild_id: int,
        channel_id: int,
        lines: int = 5,
        author_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["guild_id = ?", "channel_id = ?"]
        params: list[Any] = [int(guild_id), int(channel_id)]
        if author_id is not None:
            clauses.append("author_id = ?")
            params.append(int(author_id))
        params.append(max(1, int(lines)))
        sql = (
            "SELECT guild_id, channel_id, message_id, author_id, author, content, timestamp, "
            "metadata_json, created_at, updated_at "
            "FROM message_logs WHERE "
            f"{' AND '.join(clauses)} "
            "ORDER BY timestamp DESC LIMIT ?"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        rows = list(reversed(rows))
        return [
            {
                "guild_id": row["guild_id"],
                "channel_id": row["channel_id"],
                "message_id": row["message_id"],
                "author_id": row["author_id"],
                "author": row["author"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_message_logs_any(
        self,
        *,
        guild_id: int | None = None,
        channel_id: int | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if guild_id is not None:
            clauses.append("guild_id = ?")
            params.append(int(guild_id))
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(int(channel_id))
        sql = (
            "SELECT guild_id, channel_id, message_id, author_id, author, content, timestamp, "
            "metadata_json, created_at, updated_at "
            "FROM message_logs WHERE "
            f"{' AND '.join(clauses)} "
            "ORDER BY timestamp ASC"
        )
        if limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "guild_id": row["guild_id"],
                "channel_id": row["channel_id"],
                "message_id": row["message_id"],
                "author_id": row["author_id"],
                "author": row["author"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


_STORE = ServerRegistryStore(SERVER_DB_PATH)


def get_server_registry() -> ServerRegistryStore:
    return _STORE
