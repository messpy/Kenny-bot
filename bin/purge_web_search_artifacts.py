#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.kennybot.utils.db import connect_database, sql_placeholders
from src.kennybot.utils.message_vector_store import MessageVectorStore
from src.kennybot.utils.paths import MESSAGE_VECTOR_SQLITE_PATH, RUNTIME_LOG_DIR, LEGACY_LOG_DIR
from src.kennybot.utils.text import looks_like_web_search_artifact


def _purge_json_messages(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, list):
        return 0

    kept: list[dict] = []
    removed = 0
    for item in data:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        content = str(item.get("content", "") or "")
        if looks_like_web_search_artifact(content):
            removed += 1
            continue
        kept.append(item)

    if removed:
        path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def _purge_message_embeddings(db_path: Path) -> int:
    store = MessageVectorStore(db_path)
    config = store._db
    if config.backend == "sqlite" and (config.sqlite_path is None or not config.sqlite_path.exists()):
        return 0
    deleted = 0
    with connect_database(config) as conn:
        cursor = conn.cursor()
        cursor.execute(sql_placeholders("SELECT message_id, content FROM message_embeddings", config))
        rows = cursor.fetchall()
        for row in rows:
            content = str(row["content"] or "")
            if not looks_like_web_search_artifact(content):
                continue
            cursor.execute(
                sql_placeholders("DELETE FROM message_embeddings WHERE message_id = ?", config),
                (int(row["message_id"]),),
            )
            deleted += 1
        conn.commit()
    return deleted


def _purge_runtime_logs(root: Path) -> int:
    total = 0
    for log_path in (
        root / RUNTIME_LOG_DIR / "events.log",
        root / LEGACY_LOG_DIR / "messages.log",
        root / "runtime" / "old" / "log" / "messages.log",
    ):
        if not log_path.exists():
            continue
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        kept = [line for line in lines if not (
            "response='全体要約" in line
            or "response='Web検索の実行に失敗しました" in line
            or "response='Web検索で予期しないエラーが発生しました" in line
        )]
        removed = len(lines) - len(kept)
        if removed:
            log_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            total += removed
    return total


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    message_files = []
    for data_root in ("server", "server_rag", "channel_rag"):
        message_files.extend((root / "data" / data_root).rglob("messages.json"))
    removed_messages = sum(_purge_json_messages(path) for path in message_files)
    removed_embeddings = _purge_message_embeddings(root / MESSAGE_VECTOR_SQLITE_PATH)
    removed_logs = _purge_runtime_logs(root)
    print(
        f"removed_messages={removed_messages} removed_embeddings={removed_embeddings} removed_logs={removed_logs}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
