from __future__ import annotations

import json
import shutil
from pathlib import Path


def _read_json_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_json_list(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_json_history(src: Path, dest: Path) -> bool:
    src_rows = _read_json_list(src)
    if not src_rows:
        src.unlink(missing_ok=True)
        return False

    if dest.exists():
        dest_rows = _read_json_list(dest)
        merged: list[dict] = []
        seen: set[int] = set()
        for row in dest_rows + src_rows:
            try:
                message_id = int(row.get("id", 0) or 0)
            except Exception:
                message_id = 0
            if message_id and message_id in seen:
                continue
            if message_id:
                seen.add(message_id)
            merged.append(row)
        _write_json_list(dest, merged)
        src.unlink(missing_ok=True)
        return True

    shutil.move(str(src), str(dest))
    return True


def migrate() -> tuple[int, int]:
    root = Path(__file__).resolve().parent.parent
    new_log_root = root / "runtime" / "logs"
    new_message_root = new_log_root / "message_logs"
    new_scoped_root = new_log_root / "channel_rag"

    migrated = 0
    skipped = 0

    legacy_scoped_root = root / "data" / "channel_rag"
    if legacy_scoped_root.exists():
        for src in legacy_scoped_root.rglob("logs/*"):
            if src.is_dir():
                continue
            rel = src.relative_to(legacy_scoped_root)
            dest = new_scoped_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest.write_text(dest.read_text(encoding="utf-8") + src.read_text(encoding="utf-8"), encoding="utf-8")
                src.unlink()
                migrated += 1
            else:
                shutil.move(str(src), str(dest))
                migrated += 1

    legacy_message_roots = [
        root / "data" / "message_logs",
        root / "runtime" / "history" / "message_logs",
        legacy_scoped_root,
    ]
    for legacy_root in legacy_message_roots:
        if not legacy_root.exists():
            continue
        if legacy_root.name == "channel_rag":
            for src in legacy_root.rglob("channels/*/messages.json"):
                try:
                    guild_id = int(src.parent.parent.parent.name)
                    channel_id = int(src.parent.parent.name)
                except Exception:
                    skipped += 1
                    continue
                dest = new_message_root / f"guild_{guild_id}_channel_{channel_id}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if _merge_json_history(src, dest):
                    migrated += 1
                else:
                    skipped += 1
            continue

        for src in legacy_root.glob("*.json"):
            dest = new_message_root / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if _merge_json_history(src, dest):
                migrated += 1
            else:
                skipped += 1

    return migrated, skipped


def main() -> int:
    migrated, skipped = migrate()
    print(f"migrated={migrated} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
