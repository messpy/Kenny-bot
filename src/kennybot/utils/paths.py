from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path("config")
DATA_DIR = Path("data")
RUNTIME_DIR = Path("runtime")
RUNTIME_LOG_DIR = RUNTIME_DIR / "logs"
RUNTIME_CACHE_DIR = RUNTIME_DIR / "cache"
RUNTIME_STATE_DIR = RUNTIME_DIR / "state"
RUNTIME_HISTORY_DIR = RUNTIME_DIR / "history"
RUNTIME_RAG_DIR = RUNTIME_DIR / "rag"
RUNTIME_TMP_DIR = RUNTIME_DIR / "tmp"
OLD_DIR = RUNTIME_DIR / "old"
LEGACY_LOG_DIR = OLD_DIR / "log"
LOG_DIR = RUNTIME_LOG_DIR
PROMPTS_DIR = Path("prompts")
KNOWLEDGE_DIR = Path("knowledge")
LEGACY_MESSAGE_LOG_DIR = DATA_DIR / "message_logs"
LEGACY_RUNTIME_MESSAGE_LOG_DIR = RUNTIME_HISTORY_DIR / "message_logs"
MESSAGE_LOG_DIR = RUNTIME_LOG_DIR / "message_logs"
MESSAGE_VECTOR_DB_PATH = RUNTIME_RAG_DIR / "message_vectors.sqlite3"
CHANNEL_RAG_DIR = DATA_DIR / "channel_rag"
# Backward-compatible alias for older references.
SERVER_RAG_DIR = CHANNEL_RAG_DIR
ALL_EVENTS_LOG = RUNTIME_LOG_DIR / "events.log"
ALL_MESSAGES_LOG = ALL_EVENTS_LOG
SCOPED_LOG_DIR = RUNTIME_LOG_DIR / "channel_rag"
RUNTIME_SETTINGS_PATH = CONFIG_DIR / "bot_settings.yaml"
LEGACY_RUNTIME_SETTINGS_PATH = DATA_DIR / "bot_settings.yaml"

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
    OLD_DIR,
    LOG_DIR,
    MESSAGE_LOG_DIR,
    SCOPED_LOG_DIR,
    PROMPTS_DIR,
    KNOWLEDGE_DIR,
):
    path.mkdir(parents=True, exist_ok=True)

LOG_FILE = ALL_EVENTS_LOG
