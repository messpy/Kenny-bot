"""会話（ChatMemory, ChatService, ChatConfig）.

将来的に bot 本体から分離してサブモジュール化しやすいよう、
会話関連の実装を src 配下へ移すための本体モジュール。
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from src.kennybot.ai.runner import OllamaRunner


@dataclass(frozen=True)
class ChatConfig:
    """会話設定"""

    model: str = "gemma2:2b"
    system_prompt: str = (
        "あなたは事実ベースで、短く明瞭に答えるアシスタントです。"
        "不足情報は推測せず、不明なら不明と言ってください。"
    )
    max_history_turns: int = 10
    max_output_chars: int = 1800
    concurrency: int = 2


class ChatMemory:
    """
    会話履歴メモリ（キー毎に管理）

    キー: (guild_id, channel_id, user_id)
    永続化はしない（将来DB化するならここを差し替え）
    """

    def __init__(self, max_turns: int = 10):
        self.max_turns = max_turns
        self._store: Dict[Tuple[int, int, int], Deque[Tuple[str, str]]] = {}

    def get(self, key: Tuple[int, int, int]) -> List[Tuple[str, str]]:
        """履歴を取得"""
        d = self._store.get(key)
        return list(d) if d else []

    def append(self, key: Tuple[int, int, int], role: str, text: str) -> None:
        """履歴に追加"""
        if key not in self._store:
            self._store[key] = deque()
        d = self._store[key]
        d.append((role, text))

        max_items = self.max_turns * 2
        while len(d) > max_items:
            d.popleft()

    def clear(self, key: Tuple[int, int, int]) -> None:
        """履歴をクリア"""
        self._store.pop(key, None)


class ChatService:
    """会話サービス（高レイヤ）"""

    def __init__(self, runner: OllamaRunner, config: ChatConfig, *, debug: bool = False):
        self.runner = runner
        self.config = config
        self.debug = debug
        self._sem = asyncio.Semaphore(config.concurrency)

    def _build_prompt(
        self,
        history: Optional[List[Tuple[str, str]]],
        user_message: str,
    ) -> str:
        """プロンプトを構築"""
        if history is None:
            history_iter: List[Tuple[str, str]] = []
        else:
            history_iter = history

        lines = [f"【System】\n{self.config.system_prompt}\n"]
        for role, text in history_iter:
            if role == "user":
                lines.append(f"【User】\n{text}\n")
            else:
                lines.append(f"【Assistant】\n{text}\n")
        lines.append(f"【User】\n{user_message}\n")
        lines.append("【Assistant】\n")
        return "\n".join(lines)

    async def chat_async(
        self,
        history: Optional[List[Tuple[str, str]]],
        user_message: str,
    ) -> str:
        """非同期で会話"""
        prompt = self._build_prompt(history, user_message)
        async with self._sem:
            out = await self.runner.run_async(prompt, model=self.config.model)

        out = out.strip()
        if len(out) > self.config.max_output_chars:
            out = out[: self.config.max_output_chars] + "\n...(省略)..."
        return out
