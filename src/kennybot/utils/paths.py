from __future__ import annotations

from datetime import datetime
from pathlib import Path


CONFIG_DIR = Path("config")
DATA_DIR = Path("data")
RUNTIME_DIR = Path("runtime")
RUNTIME_LOG_DIR = RUNTIME_DIR / "logs"
RUNTIME_CACHE_DIR = RUNTIME_DIR / "cache"
RUNTIME_STATE_DIR = RUNTIME_DIR / "state"
RUNTIME_HISTORY_DIR = RUNTIME_DIR / "history"
RUNTIME_RAG_DIR = RUNTIME_DIR / "rag"
RUNTIME_TMP_DIR = RUNTIME_DIR / "tmp"
LEGACY_LOG_DIR = Path("log")
LOG_DIR = RUNTIME_LOG_DIR
PROMPTS_DIR = Path("prompts")
KNOWLEDGE_DIR = Path("knowledge")

for path in (
    CONFIG_DIR,
    DATA_DIR,
    RUNTIME_DIR,
    RUNTIME_LOG_DIR,
    RUNTIME_CACHE_DIR,
    RUNTIME_STATE_DIR,
    RUNTIME_HISTORY_DIR,
    RUNTIME_RAG_DIR,
    RUNTIME_TMP_DIR,
    LEGACY_LOG_DIR,
    LOG_DIR,
    PROMPTS_DIR,
    KNOWLEDGE_DIR,
):
    path.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / datetime.now().strftime("kennybot_%Y%m%d.log")
MESSAGE_LOG_DIR = DATA_DIR / "message_logs"
MESSAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)
MESSAGE_VECTOR_DB_PATH = MESSAGE_LOG_DIR / "message_vectors.sqlite3"
CHANNEL_RAG_DIR = DATA_DIR / "channel_rag"
CHANNEL_RAG_DIR.mkdir(parents=True, exist_ok=True)
# Backward-compatible alias for older references.
SERVER_RAG_DIR = CHANNEL_RAG_DIR
ALL_MESSAGES_LOG = RUNTIME_LOG_DIR / "messages.log"
RUNTIME_SETTINGS_PATH = CONFIG_DIR / "bot_settings.yaml"
LEGACY_RUNTIME_SETTINGS_PATH = DATA_DIR / "bot_settings.yaml"
