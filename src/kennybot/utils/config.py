from __future__ import annotations

from dataclasses import dataclass

from src.kennybot.utils.prompts import get_prompt
from src.kennybot.utils.runtime_settings import get_settings


PROMPT_TEMPLATE = get_prompt("chat", "prompt_template")
HISTORY_CONTEXT_TEMPLATE = get_prompt("chat", "history_context_template")


@dataclass(frozen=True)
class AIModelConfig:
    default: str
    chat: str
    summary: str
    embedding: str
    timeout_sec: int


@dataclass(frozen=True)
class ChatRuntimeConfig:
    history_lines: int
    max_response_length: int
    max_response_length_prompt: int


@dataclass(frozen=True)
class SpamConfig:
    max_msgs: int
    per_seconds: float
    max_ai_calls: int
    ai_per_seconds: float
    dup_window_seconds: float
    warn_cooldown_seconds: float


class AppConfig:
    """型付き設定アクセサ。

    `runtime_settings` を直接散発的に読む代わりに、主要な設定群をここに集約する。
    新キーを優先しつつ、旧キーも fallback して互換性を保つ。
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def _get_first(self, *paths: str, default=None, guild_id: int | None = None):
        for path in paths:
            value = self._settings.get(path, None, guild_id=guild_id)
            if value is not None:
                return value
        return default

    def ai_models(self, *, guild_id: int | None = None) -> AIModelConfig:
        return AIModelConfig(
            default=str(
                self._get_first(
                    "ai.models.default",
                    "ollama.model_default",
                    default="gpt-oss:120b-cloud",
                    guild_id=guild_id,
                )
            ),
            chat=str(
                self._get_first(
                    "ai.models.chat",
                    "ollama.model_chat",
                    default="gpt-oss:120b-cloud",
                    guild_id=guild_id,
                )
            ),
            summary=str(
                self._get_first(
                    "ai.models.summary",
                    "ollama.model_summary",
                    default="gpt-oss:120b-cloud",
                    guild_id=guild_id,
                )
            ),
            embedding=str(
                self._get_first(
                    "ai.models.embedding",
                    "ollama.model_embedding",
                    default="embeddinggemma",
                    guild_id=guild_id,
                )
            ),
            timeout_sec=max(
                1,
                int(
                    self._get_first(
                        "ai.timeout_sec",
                        "ollama.timeout_sec",
                        default=180,
                        guild_id=guild_id,
                    )
                ),
            ),
        )

    def chat_runtime(self, *, guild_id: int | None = None) -> ChatRuntimeConfig:
        return ChatRuntimeConfig(
            history_lines=max(
                1,
                int(self._settings.get("chat.history_lines", 100, guild_id=guild_id)),
            ),
            max_response_length=max(
                1,
                int(self._settings.get("chat.max_response_length", 1800, guild_id=guild_id)),
            ),
            max_response_length_prompt=max(
                1,
                int(self._settings.get("chat.max_response_length_prompt", 1800, guild_id=guild_id)),
            ),
        )

    def spam(self, *, guild_id: int | None = None) -> SpamConfig:
        return SpamConfig(
            max_msgs=max(
                1,
                int(
                    self._get_first(
                        "spam.max_msgs",
                        "security.spam.max_msgs",
                        default=5,
                        guild_id=guild_id,
                    )
                ),
            ),
            per_seconds=max(
                1.0,
                float(
                    self._get_first(
                        "spam.per_seconds",
                        "security.spam.per_seconds",
                        default=8.0,
                        guild_id=guild_id,
                    )
                ),
            ),
            max_ai_calls=max(
                1,
                int(
                    self._get_first(
                        "spam.max_ai_calls",
                        "security.spam.max_ai_calls",
                        default=2,
                        guild_id=guild_id,
                    )
                ),
            ),
            ai_per_seconds=max(
                1.0,
                float(
                    self._get_first(
                        "spam.ai_per_seconds",
                        "security.spam.ai_per_seconds",
                        default=20.0,
                        guild_id=guild_id,
                    )
                ),
            ),
            dup_window_seconds=max(
                1.0,
                float(
                    self._get_first(
                        "spam.dup_window_seconds",
                        "security.spam.dup_window_seconds",
                        default=12.0,
                        guild_id=guild_id,
                    )
                ),
            ),
            warn_cooldown_seconds=max(
                1.0,
                float(
                    self._get_first(
                        "spam.warn_cooldown_seconds",
                        "security.spam.warn_cooldown_seconds",
                        default=20.0,
                        guild_id=guild_id,
                    )
                ),
            ),
        )


def get_app_config() -> AppConfig:
    return AppConfig()
