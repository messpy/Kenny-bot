from __future__ import annotations

import shutil
from pathlib import Path


def _copy_tree(src_root: Path, dest_root: Path) -> tuple[int, int]:
    migrated = 0
    skipped = 0
    if not src_root.exists():
        return migrated, skipped
    for src in src_root.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(src_root)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            skipped += 1
            continue
        shutil.copy2(src, dest)
        migrated += 1
    return migrated, skipped


def migrate() -> tuple[int, int]:
    root = Path(__file__).resolve().parent.parent
    dest_root = root / "data" / "server"
    sources = [
        root / "data" / "server_rag",
        root / "data" / "channel_rag",
    ]

    migrated = 0
    skipped = 0
    for src_root in sources:
        copied, skipped_count = _copy_tree(src_root, dest_root)
        migrated += copied
        skipped += skipped_count
    return migrated, skipped


def main() -> int:
    migrated, skipped = migrate()
    print(f"migrated={migrated} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
