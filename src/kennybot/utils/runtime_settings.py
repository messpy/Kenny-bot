# utils/runtime_settings.py
# YAML ベース設定ストア

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any
import shutil

import yaml
from src.kennybot.utils.paths import LEGACY_RUNTIME_SETTINGS_PATH, RUNTIME_SETTINGS_PATH
from src.kennybot.utils.server_registry import get_server_registry


DEFAULT_SETTINGS: dict[str, Any] = {
        "global": {
        "ai": {
            "models": {
                "default": "gpt-oss:120b",
                "chat": "gpt-oss:120b",
                "summary": "gpt-oss:120b",
                "embedding": "embeddinggemma",
                "fallback": ["qwen3.5:122b"],
            },
            "timeout_sec": 180,
        },
        "ollama": {
            "model_default": "gemini-2.5-flash",
            "model_chat": "gpt-oss:120b",
            "model_summary": "gpt-oss:120b",
            "model_embedding": "embeddinggemma",
            "timeout_sec": 180,
        },
        "chat": {
            "history_lines": 100,
            "user_history_lines": 24,
            "channel_history_lines": 16,
            "semantic_history_k": 6,
            "history_max_messages": 1000,
            "history_retention_days": 30,
            "max_response_length": 1800,
            "max_response_length_prompt": 1800,
        },
        "summarize_recent_default_messages": 30,
        "summarize_recent": {
            "history_fetch_limit": 300,
            "transcript_lines_limit": 120,
            "max_messages": 300,
        },
        "kenny_chat": {
            "cooldown_seconds": 12,
            "block_invite_and_mass_mention": True,
        },
        "weekly_ai_posts": {
            "enabled": False,
            "poll_seconds": 60,
            "items": [],
        },
        "image_generation": {
            "enabled": True,
            "provider": "gemini",
            "model": "gemini-2.5-flash-image",
            "size": "1024x1024",
        },
        "security": {
            "ai_max_concurrency": 2,
            "ai_channel_cooldown_seconds": 4,
            "max_user_message_chars": 1200,
            "spam": {
                "max_msgs": 5,
                "per_seconds": 8.0,
                "max_ai_calls": 2,
                "ai_per_seconds": 20.0,
                "dup_window_seconds": 12.0,
                "warn_cooldown_seconds": 20.0,
            },
        },
        "spam": {
            "max_msgs": 5,
            "per_seconds": 8.0,
            "max_ai_calls": 2,
            "ai_per_seconds": 20.0,
            "dup_window_seconds": 12.0,
            "warn_cooldown_seconds": 20.0,
        },
        "meeting": {
            "max_minutes": 90,
            "audio_max_total_mb": 0,
            "audio_max_user_mb": 0,
            "transcription_provider": "google_web",
            "google_language_code": "ja-JP",
            "google_chunk_seconds": 20,
            "google_timeout_sec": 90,
            "google_model": "",
            "whisper_model": "base",
            "realtime_whisper_model": "tiny",
            "realtime_translation_enabled": True,
            "translation_target_language": "ja",
            "realtime_translation_interval_sec": 15,
            "realtime_translation_min_audio_bytes": 24000,
            "recording_backend": "internal",
        },
        "voice": {
            "log_private_channels": False,
        },
        "logging": {
            "event_channel_id": 0,
            "event_channel_name": "kennybot-log",
        },
        "admin": {
            "authoritative_correction_user_ids": [387651883847909376],
        },
        "keyword_reactions": {
            "いいね": "👍",
            "ミュ": "🐈",
            "みゅ": "🐈",
            "天才": "🧠",
            "かわいい": "💕",
            "おはよう": "☀",
            "おやすみ": "🌙",
            "天使": "て、て、て、天使の羽👼",
        },
        "reactions": {
            "ai_review": "🤔",
            "weekly_today_language": {
                "unknown": "✋",
                "known": "👀",
                "learned": "✅",
                "issue": "⚠️",
            },
            "mod_reset": "🔄",
            "mod_list": "📋",
            "vc": {
                "join": "✅",
                "mute_on": "🔇",
                "mute_off": "🎤",
                "deaf_on": "🙉",
                "deaf_off": "🙊",
            },
            "group_match": {
                "join": "🤝",
                "start": "▶️",
            },
            "minutes": {
                "summary": "⏯️",
                "stop": "⏹️",
                "playback": "🎶",
                "realtime": "▶️",
            },
            "timer": {
                "restart": "🔁",
            },
            "game": {
                "join": "🎮",
                "start": "▶️",
            },
            "wordwolf": {
                "end": "⏹️",
                "repeat": "🔁",
            },
            "werewolf": {
                "votes": ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"],
            },
        },
        "reaction_roles": {
            "bindings": {},
        },
        "tts": {
            "voicevox_url": "http://127.0.0.1:50021",
            "speaker_id": 3,
            "max_chars": 120,
        },
        "external": {
            "weather_default_location": "Tokyo",
            "weather_timeout_sec": 8,
            "holiday_timeout_sec": 8,
        },
        "user_nicknames": {},
        "recorder": {
            "default_format": "flac",
            "max_minutes": 180,
            "silence_timeout_seconds": 15,
            "max_tracks": 10000,
            "auto_cook_formats": ["flac", "mix"],
        },
    },
    "guilds": {},
}


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._data: dict[str, Any] = {}
        self._last_mtime_ns: int | None = None
        self.reload()

    def _current_mtime_ns(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _maybe_reload(self) -> None:
        current = self._current_mtime_ns()
        if current != self._last_mtime_ns:
            self.reload()

    def reload(self) -> None:
        with self._lock:
            previous = deepcopy(self._data)
            if self.path.exists():
                try:
                    obj = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                    if isinstance(obj, dict):
                        self._data = obj
                    else:
                        self._data = {}
                except Exception:
                    self._data = {}
            else:
                self._data = {}
            self._ensure_shape()
            self._migrate_legacy_keys()
            self._migrate_loaded_guilds_to_registry()
            self._last_mtime_ns = self._current_mtime_ns()
            if self._data != previous:
                self.save()

    def save(self) -> None:
        with self._lock:
            payload = {
                "global": self._data.get("global", {}),
            }
            self.path.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    def _migrate_loaded_guilds_to_registry(self) -> None:
        guilds = self._data.setdefault("guilds", {})
        if not isinstance(guilds, dict):
            guilds = {}
            self._data["guilds"] = guilds
        for guild_id, guild_data in list(guilds.items()):
            try:
                guild_id_int = int(guild_id)
            except Exception:
                continue
            if not isinstance(guild_data, dict):
                continue
            try:
                get_server_registry().upsert_guild(
                    guild_id_int,
                    settings=guild_data,
                    metadata={"source": str(self.path), "storage": "database"},
                )
            except Exception:
                pass

    def _save_guild_registry(self, guild_id: int) -> None:
        try:
            guild_data = self._data.get("guilds", {}).get(str(guild_id), {})
            if not isinstance(guild_data, dict):
                guild_data = {}
            try:
                get_server_registry().upsert_guild(
                    guild_id,
                    settings=guild_data,
                    metadata={"source": str(self.path), "storage": "database"},
                )
            except Exception:
                pass
        except Exception:
            pass

    def _ensure_shape(self) -> None:
        if "global" not in self._data or not isinstance(self._data.get("global"), dict):
            self._data["global"] = {}
        if "guilds" not in self._data or not isinstance(self._data.get("guilds"), dict):
            self._data["guilds"] = {}
        self._data = self._deep_merge(deepcopy(DEFAULT_SETTINGS), self._data)

    def _migrate_legacy_keys(self) -> None:
        global_settings = self._data.setdefault("global", {})
        if not isinstance(global_settings, dict):
            global_settings = {}
            self._data["global"] = global_settings

        ai_settings = global_settings.setdefault("ai", {})
        if not isinstance(ai_settings, dict):
            ai_settings = {}
            global_settings["ai"] = ai_settings
        ai_models = ai_settings.setdefault("models", {})
        if not isinstance(ai_models, dict):
            ai_models = {}
            ai_settings["models"] = ai_models

        ollama_settings = global_settings.get("ollama", {})
        if isinstance(ollama_settings, dict):
            model_mapping = {
                "model_default": "default",
                "model_chat": "chat",
                "model_summary": "summary",
                "model_embedding": "embedding",
            }
            for old_key, new_key in model_mapping.items():
                if new_key not in ai_models and old_key in ollama_settings:
                    ai_models[new_key] = ollama_settings[old_key]
            if "timeout_sec" not in ai_settings and "timeout_sec" in ollama_settings:
                ai_settings["timeout_sec"] = ollama_settings["timeout_sec"]
            global_settings.pop("ollama", None)

        spam_settings = global_settings.setdefault("spam", {})
        if not isinstance(spam_settings, dict):
            spam_settings = {}
            global_settings["spam"] = spam_settings

        security_settings = global_settings.get("security", {})
        if isinstance(security_settings, dict):
            legacy_spam = security_settings.get("spam", {})
            if isinstance(legacy_spam, dict):
                for key, value in legacy_spam.items():
                    spam_settings.setdefault(key, value)
                security_settings.pop("spam", None)

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = self._deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    def _get_by_path(self, root: dict[str, Any], path: str, default: Any) -> Any:
        cur: Any = root
        for p in path.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def _set_by_path(self, root: dict[str, Any], path: str, value: Any) -> None:
        cur: dict[str, Any] = root
        parts = path.split(".")
        for p in parts[:-1]:
            node = cur.get(p)
            if not isinstance(node, dict):
                node = {}
                cur[p] = node
            cur = node
        cur[parts[-1]] = value

    def get(self, path: str, default: Any = None, guild_id: int | None = None) -> Any:
        self._maybe_reload()
        with self._lock:
            if guild_id is not None:
                g: dict[str, Any] = {}
                try:
                    registry_guild = get_server_registry().get_guild(int(guild_id))
                except Exception:
                    registry_guild = None
                if isinstance(registry_guild, dict):
                    registry_settings = registry_guild.get("settings", {})
                    if isinstance(registry_settings, dict):
                        g = self._deep_merge(g, registry_settings)
                local_guild = self._data["guilds"].get(str(guild_id), {})
                if isinstance(local_guild, dict):
                    g = self._deep_merge(g, local_guild)
                val = self._get_by_path(g, path, None)
                if val is not None:
                    return val
            return self._get_by_path(self._data["global"], path, default)

    def set(self, path: str, value: Any, guild_id: int | None = None) -> None:
        with self._lock:
            if guild_id is None:
                self._set_by_path(self._data["global"], path, value)
            else:
                g = self._data["guilds"].setdefault(str(guild_id), {})
                if not isinstance(g, dict):
                    g = {}
                    self._data["guilds"][str(guild_id)] = g
                self._set_by_path(g, path, value)
            self.save()
            if guild_id is not None:
                self._save_guild_registry(guild_id)

    def get_global_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data["global"])


if not RUNTIME_SETTINGS_PATH.exists() and LEGACY_RUNTIME_SETTINGS_PATH.exists():
    RUNTIME_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LEGACY_RUNTIME_SETTINGS_PATH, RUNTIME_SETTINGS_PATH)

SETTINGS_PATH = RUNTIME_SETTINGS_PATH
_STORE = SettingsStore(SETTINGS_PATH)


def get_settings() -> SettingsStore:
    return _STORE
