from __future__ import annotations

from pathlib import Path

from src.kennybot.utils.paths import DATA_DIR


SCOPED_DATA_DIR = DATA_DIR / "channel_rag"


def guild_scope_dir(guild_id: int) -> Path:
    return SCOPED_DATA_DIR / str(int(guild_id))


def channel_scope_dir(guild_id: int, channel_id: int) -> Path:
    return guild_scope_dir(guild_id) / "channels" / str(int(channel_id))


def guild_logs_dir(guild_id: int) -> Path:
    return guild_scope_dir(guild_id) / "logs"


def channel_logs_dir(guild_id: int, channel_id: int) -> Path:
    return channel_scope_dir(guild_id, channel_id) / "logs"


def guild_settings_path(guild_id: int) -> Path:
    return guild_scope_dir(guild_id) / "settings.yaml"


def guild_rules_path(guild_id: int) -> Path:
    return guild_scope_dir(guild_id) / "rules.md"


def channel_chat_rag_path(guild_id: int, channel_id: int) -> Path:
    return channel_scope_dir(guild_id, channel_id) / "chat_rag.md"


def ensure_scoped_dirs(guild_id: int, channel_id: int | None = None) -> None:
    guild_scope_dir(guild_id).mkdir(parents=True, exist_ok=True)
    guild_logs_dir(guild_id).mkdir(parents=True, exist_ok=True)
    if channel_id is not None:
        channel_scope_dir(guild_id, channel_id).mkdir(parents=True, exist_ok=True)
        channel_logs_dir(guild_id, channel_id).mkdir(parents=True, exist_ok=True)


def append_text(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line.rstrip("\n") + "\n")
