# bot.py
# MyBot メインクラス

import logging
import os
import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.utils.app_settings import OLLAMA_MODEL_DEFAULT, OLLAMA_MODEL_CHAT, OLLAMA_MODEL_SUMMARY, OLLAMA_TIMEOUT_SEC, MAX_RESPONSE_LENGTH
from src.kennybot.ai.runner import OllamaRunner, OllamaConfig
from src.kennybot.features.chat import ChatMemory, ChatService, ChatConfig
from src.kennybot.ai.client import OllamaClientService, OllamaClientConfig, create_ollama_client
from src.kennybot.ai.search import AISearchService, DuckDuckGoSearch, SearchConfig, SummaryConfig, WebSummarizer
from src.kennybot.guards.spam_guard import SpamGuard, SpamPolicy
from src.kennybot.cogs.voice_logger import VoiceLogger
from src.kennybot.cogs.member_logger import MemberLogger
from src.kennybot.cogs.audit_logger import AuditLogger
from src.kennybot.cogs.message_logger import MessageLogger
from src.kennybot.cogs.mod_panel import ModPanel
from src.kennybot.cogs.reaction_roles import ReactionRoles
from src.kennybot.cogs.slash_commands import SlashCommands
from src.kennybot.cogs.tts_reader import TTSReader
from src.kennybot.cogs.game_commands import GameCommands
from src.kennybot.utils.meeting_minutes import MeetingMinutesManager
from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.voice_recv_patch import apply_voice_recv_resilience_patch
from src.kennybot.utils.ai_progress import AIProgressTracker
from src.kennybot.utils.logger import install_asyncio_exception_handler


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
        ai_concurrency = min(2, max(1, int(settings.get("security.ai_max_concurrency", 2))))
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
        self.ai_progress_tracker = AIProgressTracker(ai_concurrency)
        self._recent_event_errors: dict[tuple[str, str], float] = {}

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

        try:
            self.ai_search = AISearchService(
                searcher=DuckDuckGoSearch(
                    SearchConfig(
                        top_n=3,
                        max_results=10,
                        timelimit="w",
                        region="jp-jp",
                        safesearch="moderate",
                        prefer_news=False,
                    )
                ),
                summarizer=WebSummarizer(
                    runner=runner,
                    config=SummaryConfig(
                        mode="normal",
                        concurrency=2,
                        model=OLLAMA_MODEL_CHAT,
                        fallback_models=(
                            OLLAMA_MODEL_SUMMARY,
                            OLLAMA_MODEL_DEFAULT,
                        ),
                        max_chars=400,
                    ),
                ),
                runner=runner,
                final_model=OLLAMA_MODEL_CHAT,
                final_fallback_models=[
                    OLLAMA_MODEL_SUMMARY,
                    OLLAMA_MODEL_DEFAULT,
                ],
                debug=False,
            )
        except Exception:
            logger.exception("Failed to initialize AI search service")
            self.ai_search = None

        # 方法2: ollama_util.py スタイルの Client API
        # ローカルの ollama を使う場合
        ollama_host = os.getenv("OLLAMA_HOST")
        if ollama_host:
            logger.info("Using remote Ollama: %s", ollama_host)
            self.ollama_client = create_ollama_client(host=ollama_host)
        else:
            logger.info("Using local Ollama (http://localhost:11434)")
            self.ollama_client = create_ollama_client()

        ollama_embed_host = os.getenv("OLLAMA_EMBED_HOST")
        if ollama_embed_host:
            logger.info("Using dedicated embed Ollama host: %s", ollama_embed_host)
            self.ollama_embed_client = create_ollama_client(host=ollama_embed_host)
        else:
            self.ollama_embed_client = self.ollama_client

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
        self.tree.on_error = self.on_app_command_error
        await self.add_cog(VoiceLogger(self))
        await self.add_cog(MemberLogger(self))
        await self.add_cog(AuditLogger(self))
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
                global_commands = list(self.tree.get_commands())
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
                logger.info("Cleared remote global slash commands")
                for command in global_commands:
                    self.tree.add_command(command)
                for guild in self.guilds:
                    try:
                        self.tree.clear_commands(guild=guild)
                        self.tree.copy_global_to(guild=guild)
                        guild_synced = await self.tree.sync(guild=guild)
                        logger.info("Guild slash commands synced: guild=%s count=%d", guild.id, len(guild_synced))
                    except Exception:
                        logger.exception("Failed to sync guild slash commands: %s", guild.id)
                        await send_event_log(
                            self,
                            guild=guild,
                            level="error",
                            title="スラッシュコマンド同期失敗",
                            description="ギルド単位のスラッシュコマンド同期に失敗しました。",
                            fields=[
                                ("ギルド", f"{guild.name} ({guild.id})", False),
                            ],
                        )
            except Exception:
                logger.exception("Failed to sync slash commands")
                await send_event_log(
                    self,
                    level="error",
                    title="スラッシュコマンド同期失敗",
                    description="グローバルなスラッシュコマンド同期に失敗しました。",
                )
            self._tree_synced = True
        logger.info("=== Bot Ready as %s ===", self.user)

    async def setup_hook(self) -> None:
        install_asyncio_exception_handler(self.loop)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        logger.exception("Unhandled app command error", exc_info=error)
        await send_event_log(
            self,
            guild=interaction.guild,
            level="error",
            title="未処理スラッシュコマンド例外",
            description="グローバルハンドラでスラッシュコマンド例外を捕捉しました。",
            fields=[
                ("コマンド", interaction.command.qualified_name if interaction.command else "unknown", True),
                ("ユーザー", f"{interaction.user} ({interaction.user.id})", False),
                ("チャンネル", str(interaction.channel_id), True),
                ("エラー", str(error)[:1000], False),
            ],
        )
        text = f"コマンド実行に失敗しました: {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            logger.exception("Failed to send global slash command error response")

    async def on_error(self, event_method: str, *args, **kwargs):
        tb = traceback.format_exc(limit=8)
        tb_key = "\n".join(tb.strip().splitlines()[-4:]) if tb else ""
        error_key = (event_method, tb_key)
        now = time.monotonic()
        last_seen = self._recent_event_errors.get(error_key, 0.0)
        if now - last_seen < 3.0:
            logger.warning("Suppressed duplicate Discord event error: %s", event_method)
            return
        self._recent_event_errors[error_key] = now
        logger.exception("Unhandled Discord event error: %s", event_method)
        await send_event_log(
            self,
            level="error",
            title="未処理イベント例外",
            description="Discord イベント処理中に未処理例外が発生しました。",
            fields=[
                ("イベント", event_method, True),
                ("例外", tb[:1000] if tb else "traceback unavailable", False),
            ],
        )
