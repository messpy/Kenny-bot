"""会話関連の互換ラッパー。

実体は src/kennybot/features/chat/ に移し、既存 import は維持する。
"""

from src.kennybot.features.chat import ChatConfig, ChatMemory, ChatService

__all__ = ["ChatConfig", "ChatMemory", "ChatService"]
