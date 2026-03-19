# bot.py
# MyBot メインクラス

import logging
import os

import discord
from discord.ext import commands

from utils.config import OLLAMA_MODEL_DEFAULT, OLLAMA_MODEL_CHAT, OLLAMA_TIMEOUT_SEC, MAX_RESPONSE_LENGTH
from ai.runner import OllamaRunner, OllamaConfig
from ai.chat import ChatMemory, ChatService, ChatConfig
from ai.client import OllamaClientService, OllamaClientConfig, create_ollama_client
from ai.search import AISearchService  # search.py から移動
from guards.spam_guard import SpamGuard, SpamPolicy
from cogs.voice_logger import VoiceLogger
from cogs.member_logger import MemberLogger
from cogs.message_logger import MessageLogger
from cogs.mod_panel import ModPanel
from cogs.reaction_roles import ReactionRoles
from cogs.slash_commands import SlashCommands
from cogs.tts_reader import TTSReader
from cogs.game_commands import GameCommands
from utils.meeting_minutes import MeetingMinutesManager
from utils.runtime_settings import get_settings
from utils.voice_recv_patch import apply_voice_recv_resilience_patch


logger = logging.getLogger(__name__)
apply_voice_recv_resilience_patch()


class MyBot(commands.Bot):
    """Discord Bot メインクラス"""

    def __init__(self, *args, **kwargs):
        # 既定helpを無効化してカスタムhelpを使用
        kwargs.setdefault("help_command", None)
        super().__init__(*args, **kwargs)

        # Spam Guard（設定から読み込み）
        settings = get_settings()
        self.spam_guard = SpamGuard(
            SpamPolicy(
                max_msgs=max(1, int(settings.get("security.spam.max_msgs", 5))),
                per_seconds=max(1.0, float(settings.get("security.spam.per_seconds", 8.0))),
                max_ai_calls=max(1, int(settings.get("security.spam.max_ai_calls", 2))),
                ai_per_seconds=max(1.0, float(settings.get("security.spam.ai_per_seconds", 20.0))),
                dup_window_seconds=max(1.0, float(settings.get("security.spam.dup_window_seconds", 12.0))),
                warn_cooldown_seconds=max(1.0, float(settings.get("security.spam.warn_cooldown_seconds", 20.0))),
            )
        )
        self.meeting_minutes = MeetingMinutesManager()

        # AI: Ollama（2つの方法を用意）
        # 方法1: subprocess/asyncio ベース（旧）
        runner = OllamaRunner(
            OllamaConfig(model=OLLAMA_MODEL_DEFAULT, timeout_sec=OLLAMA_TIMEOUT_SEC),
            debug=False,
        )

        # Chat（subprocess/asyncio）
        self.chat_memory = ChatMemory(max_turns=10)
        self.chat_service = ChatService(
            runner=runner,
            config=ChatConfig(
                model=OLLAMA_MODEL_CHAT,
                max_history_turns=10,
                max_output_chars=MAX_RESPONSE_LENGTH,
                concurrency=2,
            ),
            debug=False,
        )

        # 方法2: ollama_util.py スタイルの Client API
        # ローカルの ollama を使う場合
        ollama_host = os.getenv("OLLAMA_HOST")
        if ollama_host:
            logger.info("Using remote Ollama: %s", ollama_host)
            self.ollama_client = create_ollama_client(host=ollama_host)
        else:
            logger.info("Using local Ollama (http://localhost:11434)")
            self.ollama_client = create_ollama_client()

        # Bot 用に設定を保持
        self.ollama_model = OLLAMA_MODEL_DEFAULT
        self._tree_synced = False

        # リモート ollama を使う場合（環境変数 OLLAMA_HOST で指定）
        # self.ollama_client = create_ollama_client(
        #     host="https://ollama.com",
        #     api_key_env="OLLAMA_API_KEY"
        # )

        # Search + Summary
        # TODO: search.py から DuckDuckGoSearch, Summarizer, AISearchService を移動
        # self.ai_search = AISearchService(...)

    async def setup_hook(self):
        """Bot セットアップ（Cog登録）"""
        await self.add_cog(VoiceLogger(self))
        await self.add_cog(MemberLogger(self))
        await self.add_cog(MessageLogger(self))
        await self.add_cog(ModPanel(self))
        await self.add_cog(ReactionRoles(self))
        await self.add_cog(SlashCommands(self))
        await self.add_cog(TTSReader(self))
        await self.add_cog(GameCommands(self))

    async def on_ready(self):
        """Bot 起動完了"""
        if not self._tree_synced:
            try:
                synced = await self.tree.sync()
                logger.info("Slash commands synced: %d", len(synced))
            except Exception:
                logger.exception("Failed to sync slash commands")
            self._tree_synced = True
        logger.info("=== Bot Ready as %s ===", self.user)
