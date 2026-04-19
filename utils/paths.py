from __future__ import annotations

from datetime import datetime
from pathlib import Path


CONFIG_DIR = Path("config")
DATA_DIR = Path("data")
LOG_DIR = Path("log")
PROMPTS_DIR = Path("prompts")
KNOWLEDGE_DIR = Path("knowledge")

for path in (CONFIG_DIR, DATA_DIR, LOG_DIR, PROMPTS_DIR, KNOWLEDGE_DIR):
    path.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / datetime.now().strftime("kennybot_%Y%m%d.log")
MESSAGE_LOG_DIR = DATA_DIR / "message_logs"
MESSAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)
MESSAGE_VECTOR_DB_PATH = MESSAGE_LOG_DIR / "message_vectors.sqlite3"
CHANNEL_RAG_DIR = DATA_DIR / "channel_rag"
CHANNEL_RAG_DIR.mkdir(parents=True, exist_ok=True)
# Backward-compatible alias for older references.
SERVER_RAG_DIR = CHANNEL_RAG_DIR
ALL_MESSAGES_LOG = LOG_DIR / "messages.log"
RUNTIME_SETTINGS_PATH = CONFIG_DIR / "bot_settings.yaml"
LEGACY_RUNTIME_SETTINGS_PATH = DATA_DIR / "bot_settings.yaml"
