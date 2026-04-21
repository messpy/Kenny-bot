#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.kennybot.utils.paths import MESSAGE_VECTOR_DB_PATH, RUNTIME_LOG_DIR, LEGACY_LOG_DIR
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
    if not db_path.exists():
        return 0

    deleted = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT message_id, content FROM message_embeddings").fetchall()
        for row in rows:
            content = str(row["content"] or "")
            if not looks_like_web_search_artifact(content):
                continue
            conn.execute("DELETE FROM message_embeddings WHERE message_id = ?", (int(row["message_id"]),))
            deleted += 1
    return deleted


def _purge_runtime_logs(root: Path) -> int:
    total = 0
    for log_path in (
        root / RUNTIME_LOG_DIR / "messages.log",
        root / LEGACY_LOG_DIR / "messages.log",
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
    message_files = list((root / "data" / "channel_rag").rglob("messages.json"))
    removed_messages = sum(_purge_json_messages(path) for path in message_files)
    removed_embeddings = _purge_message_embeddings(root / MESSAGE_VECTOR_DB_PATH)
    removed_logs = _purge_runtime_logs(root)
    print(
        f"removed_messages={removed_messages} removed_embeddings={removed_embeddings} removed_logs={removed_logs}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
