#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


def _legacy_targets(root: Path) -> list[Path]:
    return [
        root / "data" / "channel_rag",
        root / "data" / "server_rag",
        root / "data" / "message_logs",
        root / "data" / "meeting_audio_debug",
        root / "data" / "server" / "server.sqlite3",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive legacy Kenny-bot data paths under runtime/old")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--apply", action="store_true", help="Move files instead of printing the plan")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    candidates = [path for path in _legacy_targets(root) if path.exists()]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_root = root / "runtime" / "old" / f"data_legacy_{stamp}"

    plan: list[dict[str, str]] = []
    for src in candidates:
        dest = archive_root / src.relative_to(root / "data")
        plan.append({"src": str(src), "dest": str(dest)})

    if not args.apply:
        print(json.dumps({"ok": True, "mode": "dry-run", "moves": plan}, ensure_ascii=False, indent=2))
        return 0

    moved = 0
    for item in plan:
        src = Path(item["src"])
        dest = Path(item["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved += 1

    print(json.dumps({"ok": True, "mode": "apply", "archive_root": str(archive_root), "moved": moved}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
