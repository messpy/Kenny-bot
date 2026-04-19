from __future__ import annotations

import shutil
from pathlib import Path


def migrate() -> tuple[int, int]:
    root = Path(__file__).resolve().parent.parent
    legacy_root = root / "data" / "server_rag"
    channel_root = root / "data" / "channel_rag"

    if not legacy_root.exists():
        return 0, 0

    migrated = 0
    skipped = 0
    for src in legacy_root.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(legacy_root)
        dest = channel_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            skipped += 1
            continue
        shutil.copy2(src, dest)
        migrated += 1

    return migrated, skipped


def main() -> int:
    migrated, skipped = migrate()
    print(f"migrated={migrated} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
