from __future__ import annotations

import json
import os
from pathlib import Path

from ai.client import create_ollama_client
from utils.message_vector_store import MessageVectorStore
from utils.paths import MESSAGE_LOG_DIR, MESSAGE_VECTOR_DB_PATH
from utils.runtime_settings import get_settings


def _iter_message_logs(root: Path) -> list[tuple[int, int, Path]]:
    out: list[tuple[int, int, Path]] = []
    for path in sorted(root.glob("guild_*_channel_*.json")):
        stem = path.stem
        parts = stem.split("_")
        if len(parts) < 4:
            continue
        try:
            guild_id = int(parts[1])
            channel_id = int(parts[3])
        except Exception:
            continue
        out.append((guild_id, channel_id, path))
    return out


def main() -> int:
    settings = get_settings()
    logs_root = MESSAGE_LOG_DIR
    vector_db = MESSAGE_VECTOR_DB_PATH
    model = str(settings.get("ollama.model_embedding", "embeddinggemma"))
    batch_size = 32

    client = create_ollama_client(host=os.getenv("OLLAMA_HOST"))
    store = MessageVectorStore(vector_db)

    total_rows = 0
    indexed_rows = 0

    for guild_id, channel_id, path in _iter_message_logs(logs_root):
        try:
            messages = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(messages, list):
            continue

        pending: list[dict] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            try:
                message_id = int(msg.get("id", 0) or 0)
            except Exception:
                message_id = 0
            if message_id <= 0 or store.has_message(message_id):
                continue
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            pending.append(
                {
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "author_id": int(msg.get("author_id", 0) or 0),
                    "author": str(msg.get("author") or "Unknown"),
                    "content": content,
                    "timestamp": str(msg.get("timestamp") or ""),
                }
            )

        total_rows += len(pending)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            texts = [row["content"] for row in batch]
            try:
                embeddings = client.embed(model=model, input_texts=texts)
            except Exception as e:
                print(f"[warn] embed failed for {path.name}: {e}")
                break
            rows: list[dict] = []
            for row, embedding in zip(batch, embeddings):
                item = dict(row)
                item["embedding"] = embedding
                rows.append(item)
            store.upsert_messages(rows)
            indexed_rows += len(rows)
            print(f"[indexed] {path.name} {start + len(rows)}/{len(pending)}")

    print(f"[done] indexed={indexed_rows} pending_seen={total_rows} db={vector_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
