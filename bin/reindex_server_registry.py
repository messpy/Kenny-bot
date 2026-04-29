#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.utils.env import load_env_file
from src.kennybot.utils.paths import SERVER_DIR
from src.kennybot.utils.server_registry import ServerRegistryStore, create_server_registry


load_env_file()


RAG_FILENAMES = (
    "chat_rag.md",
    "chat_rag.json",
    "chat_rag.toml",
    "faq.md",
    "faq.json",
    "rules.md",
    "rules.json",
    "rules.toml",
)


def _short_summary(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".json", ".toml"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_yaml(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _record_directory(
    store: ServerRegistryStore,
    *,
    guild_id: int,
    directory: Path,
    scope: str,
    channel_id: int | None = None,
) -> int:
    written = 0
    for name in RAG_FILENAMES:
        path = directory / name
        if not path.exists():
            continue
        body = _load_text(path)
        store.upsert_rag_document(
            scope=scope,
            guild_id=guild_id,
            channel_id=channel_id,
            source_path=path,
            doc_type=path.suffix.lstrip(".") or "text",
            title=path.stem,
            summary=_short_summary(body),
            body=body,
            metadata={"source": "reindex_server_registry"},
        )
        written += 1
    return written


def _legacy_source_roots(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in (
        root / SERVER_DIR,
        root / "data" / "server_rag",
        root / "data" / "channel_rag",
    ):
        if path.exists() and path not in candidates:
            candidates.append(path)
    archived_roots = sorted((root / "runtime" / "old").glob("data_legacy_*/server_rag"))
    archived_roots.extend(sorted((root / "runtime" / "old").glob("data_legacy_*/channel_rag")))
    for path in archived_roots:
        if path.exists() and path not in candidates:
            candidates.append(path)
    return candidates


def _scan_source_root(store: ServerRegistryStore, source_root: Path) -> tuple[int, int, int]:
    guild_count = 0
    channel_count = 0
    doc_count = 0
    seen_guilds: set[int] = set()
    seen_channels: set[tuple[int, int]] = set()

    for guild_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
        try:
            guild_id = int(guild_dir.name)
        except ValueError:
            continue

        guild_settings = {}
        guild_settings_path = guild_dir / "settings.yaml"
        if guild_settings_path.exists():
            guild_settings = _load_yaml(guild_settings_path)
        store.upsert_guild(
            guild_id,
            settings=guild_settings,
            metadata={"source": str(guild_dir)},
        )
        if guild_id not in seen_guilds:
            seen_guilds.add(guild_id)
            guild_count += 1
        doc_count += _record_directory(store, guild_id=guild_id, directory=guild_dir, scope="guild")

        channels_dir = guild_dir / "channels"
        if not channels_dir.exists():
            continue
        for channel_dir in sorted(p for p in channels_dir.iterdir() if p.is_dir()):
            try:
                channel_id = int(channel_dir.name)
            except ValueError:
                continue
            channel_settings = {}
            channel_settings_path = channel_dir / "settings.yaml"
            if channel_settings_path.exists():
                channel_settings = _load_yaml(channel_settings_path)
            store.upsert_channel(
                guild_id,
                channel_id,
                settings=channel_settings,
                metadata={"source": str(channel_dir)},
            )
            key = (guild_id, channel_id)
            if key not in seen_channels:
                seen_channels.add(key)
                channel_count += 1
            doc_count += _record_directory(
                store,
                guild_id=guild_id,
                directory=channel_dir,
                scope="channel",
                channel_id=channel_id,
            )
    return guild_count, channel_count, doc_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex data/server into the configured Kennybot DB")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    server_root = root / SERVER_DIR
    store = create_server_registry(root)

    source_roots = _legacy_source_roots(root)
    if not source_roots:
        print(f"[reindex_server_registry] missing sources under: {server_root}")
        return 1
    total_guilds = 0
    total_channels = 0
    total_documents = 0
    seen_guilds: set[int] = set()
    seen_channels: set[tuple[int, int]] = set()
    for source_root in source_roots:
        guilds, channels, documents = _scan_source_root(store, source_root)
        total_documents += documents
        for guild_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
            try:
                guild_id = int(guild_dir.name)
            except ValueError:
                continue
            if guild_id not in seen_guilds:
                seen_guilds.add(guild_id)
                total_guilds += 1
            channels_dir = guild_dir / "channels"
            if not channels_dir.exists():
                continue
            for channel_dir in sorted(p for p in channels_dir.iterdir() if p.is_dir()):
                try:
                    channel_id = int(channel_dir.name)
                except ValueError:
                    continue
                key = (guild_id, channel_id)
                if key not in seen_channels:
                    seen_channels.add(key)
                    total_channels += 1

    print(
        json.dumps(
            {
                "ok": True,
                "guilds": total_guilds,
                "channels": total_channels,
                "documents": total_documents,
                "sources": [str(path) for path in source_roots],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
