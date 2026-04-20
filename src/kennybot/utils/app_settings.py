from __future__ import annotations

from src.kennybot.utils.runtime_settings import get_settings


_settings = get_settings()

OLLAMA_MODEL_DEFAULT = str(_settings.get("ollama.model_default", "gpt-oss:120b"))
OLLAMA_MODEL_CHAT = str(_settings.get("ollama.model_chat", OLLAMA_MODEL_DEFAULT))
OLLAMA_MODEL_SUMMARY = str(_settings.get("ollama.model_summary", OLLAMA_MODEL_DEFAULT))
OLLAMA_TIMEOUT_SEC = int(_settings.get("ollama.timeout_sec", 180))

CHAT_HISTORY_LINES = int(_settings.get("chat.history_lines", 100))
MAX_RESPONSE_LENGTH = int(_settings.get("chat.max_response_length", 1800))
MAX_RESPONSE_LENGTH_PROMPT = int(_settings.get("chat.max_response_length_prompt", 500))
KEYWORD_REACTIONS = dict(_settings.get("keyword_reactions", {}))

_user_nicks_raw = dict(_settings.get("user_nicknames", {}))
USER_NICKNAMES: dict[int, str] = {}
for _key, _value in _user_nicks_raw.items():
    try:
        USER_NICKNAMES[int(_key)] = str(_value)
    except Exception:
        continue
