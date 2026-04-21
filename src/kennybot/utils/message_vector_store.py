from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from src.kennybot.utils.text import looks_like_web_search_artifact


class MessageVectorStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_embeddings (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL PRIMARY KEY,
                    author_id INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    embedding_json TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_embed_channel_time "
                "ON message_embeddings (guild_id, channel_id, timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_embed_author_time "
                "ON message_embeddings (guild_id, channel_id, author_id, timestamp DESC)"
            )

    def upsert_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        author_id: int,
        author: str,
        content: str,
        timestamp: str,
        embedding: list[float] | None,
    ) -> None:
        embedding_json = json.dumps(embedding) if embedding else None
        if looks_like_web_search_artifact(content):
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO message_embeddings (
                    guild_id, channel_id, message_id, author_id, author, content, timestamp, embedding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    guild_id=excluded.guild_id,
                    channel_id=excluded.channel_id,
                    author_id=excluded.author_id,
                    author=excluded.author,
                    content=excluded.content,
                    timestamp=excluded.timestamp,
                    embedding_json=COALESCE(excluded.embedding_json, message_embeddings.embedding_json)
                """,
                (guild_id, channel_id, message_id, author_id, author, content, timestamp, embedding_json),
            )

    def upsert_messages(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        payload = []
        for row in rows:
            embedding = row.get("embedding")
            content = str(row["content"])
            if looks_like_web_search_artifact(content):
                continue
            payload.append(
                (
                    int(row["guild_id"]),
                    int(row["channel_id"]),
                    int(row["message_id"]),
                    int(row["author_id"]),
                    str(row["author"]),
                    str(row["content"]),
                    str(row["timestamp"]),
                    json.dumps(embedding) if embedding else None,
                )
            )
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO message_embeddings (
                    guild_id, channel_id, message_id, author_id, author, content, timestamp, embedding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    guild_id=excluded.guild_id,
                    channel_id=excluded.channel_id,
                    author_id=excluded.author_id,
                    author=excluded.author,
                    content=excluded.content,
                    timestamp=excluded.timestamp,
                    embedding_json=COALESCE(excluded.embedding_json, message_embeddings.embedding_json)
                """,
                payload,
            )

    def has_message(self, message_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM message_embeddings WHERE message_id = ? LIMIT 1",
                (int(message_id),),
            ).fetchone()
        return row is not None

    def semantic_search(
        self,
        *,
        guild_id: int,
        channel_id: int,
        query_embedding: list[float],
        author_id: int | None = None,
        limit: int = 6,
        sample_limit: int = 400,
    ) -> list[dict[str, Any]]:
        where = ["guild_id = ?", "channel_id = ?", "embedding_json IS NOT NULL"]
        params: list[Any] = [guild_id, channel_id]
        if author_id is not None:
            where.append("author_id = ?")
            params.append(author_id)

        sql = (
            "SELECT message_id, author_id, author, content, timestamp, embedding_json "
            "FROM message_embeddings "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(max(limit, sample_limit))

        scored: list[tuple[float, dict[str, Any]]] = []
        with self._connect() as conn:
            for row in conn.execute(sql, params):
                embedding_json = row["embedding_json"]
                if not embedding_json:
                    continue
                try:
                    candidate = json.loads(embedding_json)
                except Exception:
                    continue
                content = str(row["content"])
                if looks_like_web_search_artifact(content):
                    continue
                if not isinstance(candidate, list) or not candidate:
                    continue
                score = self._cosine_similarity(query_embedding, candidate)
                if score <= 0:
                    continue
                scored.append(
                    (
                        score,
                        {
                            "message_id": row["message_id"],
                            "author_id": row["author_id"],
                            "author": row["author"],
                            "content": row["content"],
                            "timestamp": row["timestamp"],
                            "score": score,
                        },
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:limit]]

    @staticmethod
    def format_results(rows: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for row in rows:
            author = str(row.get("author") or "Unknown")
            author_id = int(row.get("author_id") or 0)
            content = str(row.get("content") or "")
            timestamp = str(row.get("timestamp") or "")
            time_str = ""
            if timestamp:
                try:
                    time_str = timestamp.split("T")[1][:5]
                except Exception:
                    time_str = ""
            author_display = f"{author} ({author_id})" if author_id else author
            prefix = f"[{time_str}] " if time_str else ""
            lines.append(f"{prefix}{author_display}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
        norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
        if norm_a <= 0 or norm_b <= 0:
            return 0.0
        return dot / (norm_a * norm_b)
