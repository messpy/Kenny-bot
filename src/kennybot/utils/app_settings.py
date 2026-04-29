from __future__ import annotations

from src.kennybot.utils.config import get_app_config
from src.kennybot.utils.runtime_settings import get_settings


_settings = get_settings()
_app_config = get_app_config()
_ai = _app_config.ai_models()
_chat = _app_config.chat_runtime()

OLLAMA_MODEL_DEFAULT = _ai.default
OLLAMA_MODEL_CHAT = _ai.chat
OLLAMA_MODEL_SUMMARY = _ai.summary
OLLAMA_TIMEOUT_SEC = _ai.timeout_sec

CHAT_HISTORY_LINES = _chat.history_lines
MAX_RESPONSE_LENGTH = _chat.max_response_length
MAX_RESPONSE_LENGTH_PROMPT = _chat.max_response_length_prompt
KEYWORD_REACTIONS = dict(_settings.get("keyword_reactions", {}))

_user_nicks_raw = dict(_settings.get("user_nicknames", {}))
USER_NICKNAMES: dict[int, str] = {}
for _key, _value in _user_nicks_raw.items():
    try:
        USER_NICKNAMES[int(_key)] = str(_value)
    except Exception:
        continue
