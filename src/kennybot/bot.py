# bot.py
# MyBot メインクラス

import asyncio
import logging
import os
import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.utils.app_settings import MAX_RESPONSE_LENGTH
from src.kennybot.ai.runner import OllamaRunner, OllamaConfig
from src.kennybot.features.chat import ChatMemory, ChatService, ChatConfig
from src.kennybot.ai.client import OllamaClientService, OllamaClientConfig, create_ollama_client
from src.kennybot.ai.gemini_vision import GeminiVisionClient
from src.kennybot.ai.openai_vision import OpenAIVisionClient
from src.kennybot.features.games import GameCommands
from src.kennybot.features.moderation import ModPanel
from src.kennybot.features.search import AISearchService, DuckDuckGoSearch, SearchConfig, SummaryConfig, WebSummarizer
from src.kennybot.features.spam import SpamGuard, SpamPolicy
from src.kennybot.features.voice import MeetingMinutesManager, TTSReader, VoiceLogger
from src.kennybot.cogs.member_logger import MemberLogger
from src.kennybot.cogs.audit_logger import AuditLogger
from src.kennybot.cogs.message_logger import MessageLogger
from src.kennybot.cogs.reaction_roles import ReactionRoles
from src.kennybot.cogs.slash_commands import SlashCommands
from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.message_logger import log_codex_repair_mode
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.voice_recv_patch import apply_voice_recv_resilience_patch
from src.kennybot.utils.ai_progress import AIProgressTracker
from src.kennybot.utils.config import get_app_config
from src.kennybot.utils.logger import install_asyncio_exception_handler


logger = logging.getLogger(__name__)
apply_voice_recv_resilience_patch()


def _resolve_ollama_host_for_runtime(host: str | None) -> str | None:
    value = (host or "").strip()
    if not value:
        return None
    lowered = value.lower()
    if "ollama.com" in lowered:
        return None
    return value


class MyBot(commands.Bot):
    """Discord Bot メインクラス"""

    def __init__(self, *args, **kwargs):
        # 既定helpを無効化してカスタムhelpを使用
        kwargs.setdefault("help_command", None)
        super().__init__(*args, **kwargs)

        # Spam Guard（設定から読み込み）
        settings = get_settings()
        app_config = get_app_config()
        ai_models = app_config.ai_models()
        spam_config = app_config.spam()
        ai_concurrency = min(2, max(1, int(settings.get("security.ai_max_concurrency", 2))))
        self.spam_guard = SpamGuard(
            SpamPolicy(
                max_msgs=spam_config.max_msgs,
                per_seconds=spam_config.per_seconds,
                max_ai_calls=spam_config.max_ai_calls,
                ai_per_seconds=spam_config.ai_per_seconds,
                dup_window_seconds=spam_config.dup_window_seconds,
                warn_cooldown_seconds=spam_config.warn_cooldown_seconds,
            )
        )
        self.meeting_minutes = MeetingMinutesManager()
        self.ai_progress_tracker = AIProgressTracker(ai_concurrency)
        self._recent_event_errors: dict[tuple[str, str], float] = {}

        # AI: Ollama（2つの方法を用意）
        # 方法1: subprocess/asyncio ベース（旧）
        runner = OllamaRunner(
            OllamaConfig(model=ai_models.default, timeout_sec=ai_models.timeout_sec),
            debug=False,
        )

        # Chat（subprocess/asyncio）
        self.chat_memory = ChatMemory(max_turns=10)
        self.chat_service = ChatService(
            runner=runner,
            config=ChatConfig(
                model=ai_models.chat,
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
                        model=ai_models.chat,
                        fallback_models=(
                            ai_models.summary,
                            ai_models.default,
                        ),
                        max_chars=400,
                    ),
                ),
                runner=runner,
                final_model=ai_models.chat,
                final_fallback_models=[
                    ai_models.summary,
                    ai_models.default,
                ],
                debug=False,
            )
        except Exception:
            logger.exception("Failed to initialize AI search service")
            self.ai_search = None

        # 方法2: ollama_util.py スタイルの Client API
        # ローカルの ollama を使う場合
        ollama_host = _resolve_ollama_host_for_runtime(os.getenv("OLLAMA_HOST"))
        if ollama_host:
            logger.info("Using remote Ollama: %s", ollama_host)
            self.ollama_client = create_ollama_client(host=ollama_host)
        else:
            logger.info("Using local Ollama (http://localhost:11434)")
            self.ollama_client = create_ollama_client()

        openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        self.openai_vision_client = None
        if openai_api_key:
            self.openai_vision_client = OpenAIVisionClient(
                api_key=openai_api_key,
                model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "120")),
            )
        gemini_api_key = (
            os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        ).strip()
        self.gemini_vision_client = None
        if gemini_api_key:
            self.gemini_vision_client = GeminiVisionClient(
                api_key=gemini_api_key,
                model=os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash"),
                fallback_models=tuple(
                    item.strip()
                    for item in os.getenv(
                        "GEMINI_VISION_FALLBACK_MODELS",
                        "gemini-2.5-flash,gemini-flash-latest,gemini-2.0-flash-lite",
                    ).split(",")
                    if item.strip()
                ),
                base_url=os.getenv(
                    "GEMINI_API_BASE",
                    "https://generativelanguage.googleapis.com/v1beta",
                ),
                timeout_seconds=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "120")),
            )

        ollama_embed_host = _resolve_ollama_host_for_runtime(os.getenv("OLLAMA_EMBED_HOST"))
        if ollama_embed_host:
            logger.info("Using dedicated embed Ollama host: %s", ollama_embed_host)
            self.ollama_embed_client = create_ollama_client(host=ollama_embed_host)
        else:
            self.ollama_embed_client = self.ollama_client

        # Bot 用に設定を保持
        self.ollama_model = ai_models.default
        self._tree_synced = False

        # リモート ollama を使う場合（環境変数 OLLAMA_HOST で指定）
        # self.ollama_client = create_ollama_client(
        #     host="https://ollama.com",
        #     api_key_env="OLLAMA_API_KEY"
        # )

    async def setup_hook(self):
        """Bot セットアップ（Cog登録）"""
        install_asyncio_exception_handler(asyncio.get_running_loop())
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
                global_synced = await self.tree.sync()
                logger.info("Global slash commands synced: count=%d", len(global_synced))
                for guild in self.guilds:
                    try:
                        self.tree.copy_global_to(guild=guild)
                        guild_synced = await self.tree.sync(guild=guild)
                        logger.info("Guild slash commands synced: guild=%s count=%d", guild.id, len(guild_synced))
                    except Exception:
                        logger.exception("Failed to sync guild slash commands: %s", guild.id)
                        log_codex_repair_mode(
                            trigger="slash_sync_error",
                            issue=f"guild={guild.id} slash command sync failed",
                            planned_fix="スラッシュコマンド同期の失敗原因を確認し、再試行や分割同期の必要性を見直す",
                            target_area=f"slash sync guild={guild.id}",
                            level="error",
                        )
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
                log_codex_repair_mode(
                    trigger="slash_sync_error",
                    issue="global slash command sync failed",
                    planned_fix="グローバルスラッシュコマンド同期の失敗原因を確認し、再試行や同期順序を見直す",
                    target_area="slash sync global",
                    level="error",
                )
                await send_event_log(
                    self,
                    level="error",
                    title="スラッシュコマンド同期失敗",
                    description="グローバルなスラッシュコマンド同期に失敗しました。",
                )
            self._tree_synced = True
        logger.info("=== Bot Ready as %s ===", self.user)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        logger.exception("Unhandled app command error", exc_info=error)
        log_codex_repair_mode(
            msg=getattr(interaction, "message", None),
            trigger="app_command_error",
            issue=str(error),
            planned_fix="スラッシュコマンド例外の再発防止と詳細ログ記録を確認する",
            target_area=interaction.command.qualified_name if interaction.command else "unknown",
            level="error",
        )
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
        log_codex_repair_mode(
            trigger="discord_event_error",
            issue=tb or event_method,
            planned_fix="Discord イベント例外の詳細を追跡し、再発する箇所を修正する",
            target_area=event_method,
            level="error",
        )
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
