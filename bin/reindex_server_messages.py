#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.utils.env import load_env_file
from src.kennybot.utils.paths import DATA_DIR, RUNTIME_LOG_DIR, SERVER_DIR
from src.kennybot.utils.server_registry import ServerRegistryStore, create_server_registry


load_env_file()


def _load_messages(path: Path) -> list[dict]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            return [item for item in loaded if isinstance(item, dict)]
    except Exception:
        pass
    return []


def _iter_message_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("messages.json"))


def _parse_ids_from_path(path: Path) -> tuple[int, int] | None:
    parts = path.parts
    try:
        if "message_logs" in parts:
            stem = path.stem
            segs = stem.split("_")
            guild_id = int(segs[1])
            channel_id = int(segs[3])
            return guild_id, channel_id
        if "server" in parts:
            idx = parts.index("server")
            guild_id = int(parts[idx + 1])
            if parts[idx + 2] == "channels":
                channel_id = int(parts[idx + 3])
                return guild_id, channel_id
        if "channel_rag" in parts:
            idx = parts.index("channel_rag")
            guild_id = int(parts[idx + 1])
            if parts[idx + 2] == "channels":
                channel_id = int(parts[idx + 3])
                return guild_id, channel_id
    except Exception:
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex message logs into the configured Kennybot DB")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    store = create_server_registry(root)

    roots = [
        root / SERVER_DIR,
        root / "data" / "channel_rag",
        root / RUNTIME_LOG_DIR / "message_logs",
    ]

    seen: set[Path] = set()
    files: list[Path] = []
    for scan_root in roots:
        for path in _iter_message_files(scan_root):
            if path in seen:
                continue
            seen.add(path)
            files.append(path)

    imported = 0
    skipped = 0
    for path in files:
        ids = _parse_ids_from_path(path)
        if ids is None:
            skipped += 1
            continue
        guild_id, channel_id = ids
        messages = _load_messages(path)
        for msg in messages:
            try:
                message_id = int(msg.get("id", 0) or 0)
                if message_id <= 0:
                    continue
                store.upsert_message_log(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    message_id=message_id,
                    author_id=int(msg.get("author_id", 0) or 0),
                    author=str(msg.get("author", "") or ""),
                    content=str(msg.get("content", "") or ""),
                    timestamp=str(msg.get("timestamp", "") or ""),
                    metadata={"source_path": str(path)},
                )
                imported += 1
            except Exception:
                continue

    print(json.dumps({"ok": True, "imported": imported, "files": len(files), "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
