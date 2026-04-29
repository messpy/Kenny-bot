# utils/message_store.py
# メッセージ履歴の保存・読み込み管理

import logging
from datetime import datetime, timedelta
from typing import Optional, List

from src.kennybot.utils.scoped_data import channel_scope_dir
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.server_registry import get_server_registry
from src.kennybot.utils.text import looks_like_web_search_artifact
from src.kennybot.utils.time import JST, now_jst

logger = logging.getLogger(__name__)
_settings = get_settings()


class MessageStore:
    """メッセージ履歴をDB正本で保存・管理する。"""

    def __init__(
        self,
        guild_id: int,
        channel_id: int,
        *,
        guild_name: str | None = None,
        channel_name: str | None = None,
    ):
        """
        Args:
            guild_id: ギルドID
            channel_id: チャンネルID
            guild_name: ギルド名（互換用）
            channel_name: チャンネル名（互換用）
        """
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild_name = guild_name
        self.channel_name = channel_name
        self._registry = get_server_registry()

    def _load_messages(self) -> List[dict]:
        """DB からメッセージを読み込む"""
        def _filter(messages: list[dict]) -> list[dict]:
            filtered: list[dict] = []
            changed = False
            for message in messages:
                content = str(message.get("content", "") or "")
                if looks_like_web_search_artifact(content):
                    changed = True
                    continue
                filtered.append(message)
            return filtered

        try:
            rows = self._registry.list_message_logs(
                guild_id=self.guild_id,
                channel_id=self.channel_id,
                lines=int(_settings.get("chat.history_max_messages", 1000)),
            )
            if rows:
                return _filter(rows)
        except Exception as e:
            logger.debug(f"Failed to load messages from database: {e}")

        return []

    def _save_messages(self, messages: List[dict]) -> None:
        """メッセージをDBへ同期する。"""
        try:
            for message in messages:
                try:
                    message_id = int(message.get("id", 0) or 0)
                    if message_id <= 0:
                        continue
                    self._registry.upsert_message_log(
                        guild_id=self.guild_id,
                        channel_id=self.channel_id,
                        message_id=message_id,
                        author_id=int(message.get("author_id", 0) or 0),
                        author=str(message.get("author", "") or ""),
                        content=str(message.get("content", "") or ""),
                        timestamp=str(message.get("timestamp", "") or ""),
                        metadata={"source": "message_store"},
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Failed to save messages: {e}")

    def _prune_messages(self, messages: List[dict]) -> List[dict]:
        """保持期間・件数に基づいてメッセージを間引く"""
        retention_days = int(_settings.get("chat.history_retention_days", 30))
        max_messages = int(_settings.get("chat.history_max_messages", 1000))

        # 期限で間引き（0以下なら期限無効）
        if retention_days > 0:
            cutoff = now_jst() - timedelta(days=retention_days)
            kept: List[dict] = []
            for m in messages:
                ts = m.get("timestamp", "")
                content = str(m.get("content", "") or "")
                if looks_like_web_search_artifact(content):
                    continue
                try:
                    dt = datetime.fromisoformat(ts)
                except Exception:
                    dt = None
                if dt is None or dt >= cutoff:
                    kept.append(m)
            messages = kept

        # 件数で間引き
        if max_messages > 0 and len(messages) > max_messages:
            messages = messages[-max_messages:]

        return messages

    def add_message(self, author_name: str, content: str, message_id: int, author_id: int = 0) -> None:
        """メッセージを履歴に追加

        Args:
            author_name: ユーザーの表示名
            content: メッセージ内容
            message_id: Discord メッセージID
            author_id: Discord ユーザーID（個人特定用）
        """
        try:
            if looks_like_web_search_artifact(content):
                return
            messages = self._load_messages()

            # 新しいメッセージを追加
            new_msg = {
                "id": message_id,
                "author_id": author_id,
                "author": author_name,
                "content": content,
                "timestamp": now_jst().isoformat(),
            }
            messages.append(new_msg)

            # 保持設定に基づき古いメッセージを削除
            messages = self._prune_messages(messages)

            self._save_messages(messages)
        except Exception as e:
            logger.error(f"Failed to add message: {e}")

    def get_recent_context(self, lines: int = 5) -> str:
        """最近のメッセージから会話の文脈を取得（日時付き）

        Args:
            lines: 取得する過去メッセージ数

        Returns:
            フォーマット済みの会話文脈（プロンプト用）
        """
        try:
            messages = self._prune_messages(self._load_messages())

            # 最新の n 件を取得
            recent = messages[-lines:] if messages else []

            if not recent:
                return ""

            # フォーマット（日時を含める）
            context_lines = []
            for msg in recent:
                author = msg.get("author", "Unknown")
                author_id = msg.get("author_id", 0)
                content = msg.get("content", "")
                timestamp = msg.get("timestamp", "")

                # ISO形式の日時から時刻だけ抽出（HH:MM）
                time_str = ""
                if timestamp:
                    try:
                        # "2026-02-17T23:48:00+09:00" → "23:48"
                        time_str = timestamp.split("T")[1][:5]
                    except Exception:
                        logger.exception("Failed to parse timestamp in recent context")

                # ユーザーIDがあれば表示（同じ名前の人の区別用）
                author_display = author
                if author_id:
                    author_display = f"{author} ({author_id})"

                if time_str:
                    context_lines.append(f"[{time_str}] {author_display}: {content}")
                else:
                    context_lines.append(f"{author_display}: {content}")

            return "\n".join(context_lines)
        except Exception:
            logger.exception("Failed to get recent context")
            return ""

    def get_recent_messages(
        self,
        lines: int = 5,
        *,
        author_id: int | None = None,
    ) -> List[dict]:
        """最近のメッセージ一覧を取得する。

        Args:
            lines: 取得する件数
            author_id: 指定時はそのユーザーの発言だけに絞る
        """
        try:
            messages = self._prune_messages(self._load_messages())
            if author_id is not None:
                messages = [m for m in messages if int(m.get("author_id", 0) or 0) == int(author_id)]
            if lines > 0:
                messages = messages[-lines:]
            return messages
        except Exception:
            logger.exception("Failed to get recent messages")
            return []

    def format_messages(self, messages: List[dict]) -> str:
        """メッセージ一覧をプロンプト向け文字列に整形する。"""
        try:
            if not messages:
                return ""

            context_lines = []
            for msg in messages:
                author = msg.get("author", "Unknown")
                author_id = msg.get("author_id", 0)
                content = msg.get("content", "")
                timestamp = msg.get("timestamp", "")

                time_str = ""
                if timestamp:
                    try:
                        time_str = timestamp.split("T")[1][:5]
                    except Exception:
                        logger.exception("Failed to parse timestamp in formatted messages")

                author_display = author
                if author_id:
                    author_display = f"{author} ({author_id})"

                if time_str:
                    context_lines.append(f"[{time_str}] {author_display}: {content}")
                else:
                    context_lines.append(f"{author_display}: {content}")
            return "\n".join(context_lines)
        except Exception:
            logger.exception("Failed to format messages")
            return ""

    def get_recent_context_for_user(self, author_id: int, lines: int = 5) -> str:
        """指定ユーザーの最近の発言だけを整形して返す。"""
        return self.format_messages(self.get_recent_messages(lines=lines, author_id=author_id))
