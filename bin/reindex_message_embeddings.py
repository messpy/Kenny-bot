#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.ai.client import create_ollama_client
from src.kennybot.utils.message_vector_store import MessageVectorStore
from src.kennybot.utils.paths import MESSAGE_VECTOR_SQLITE_PATH
from src.kennybot.utils.server_registry import create_server_registry
from src.kennybot.utils.runtime_settings import get_settings


def main() -> int:
    settings = get_settings()
    model = str(settings.get("ollama.model_embedding", "embeddinggemma"))
    batch_size = 32

    client = create_ollama_client(host=os.getenv("OLLAMA_HOST"))
    vector_store = MessageVectorStore(MESSAGE_VECTOR_SQLITE_PATH)
    registry = create_server_registry()

    rows = registry.list_message_logs_any()
    pending: list[dict] = []
    for row in rows:
        message_id = int(row.get("message_id", 0) or 0)
        if message_id <= 0 or vector_store.has_message(message_id):
            continue
        content = str(row.get("content", "") or "").strip()
        if not content:
            continue
        pending.append(
            {
                "guild_id": int(row["guild_id"]),
                "channel_id": int(row["channel_id"]),
                "message_id": message_id,
                "author_id": int(row.get("author_id", 0) or 0),
                "author": str(row.get("author", "Unknown") or "Unknown"),
                "content": content,
                "timestamp": str(row.get("timestamp", "") or ""),
            }
        )

    indexed_rows = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        texts = [row["content"] for row in batch]
        try:
            embeddings = client.embed(model=model, input_texts=texts)
        except Exception as e:
            print(f"[warn] embed failed: {e}")
            break
        payload: list[dict] = []
        for row, embedding in zip(batch, embeddings):
            item = dict(row)
            item["embedding"] = embedding
            payload.append(item)
        vector_store.upsert_messages(payload)
        indexed_rows += len(payload)
        print(f"[indexed] {start + len(payload)}/{len(pending)}")

    print(f"[done] indexed={indexed_rows} pending_seen={len(pending)} backend={vector_store._db.backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
