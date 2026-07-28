# bot.py
# MyBot メインクラス

import asyncio
import logging
import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.features.games import GameCommands
from src.kennybot.cogs.birthday_reminders import BirthdayReminders
from src.kennybot.features.moderation import ModPanel
from src.kennybot.features.voice import TTSReader, VoiceLogger
from src.kennybot.cogs.member_logger import MemberLogger
from src.kennybot.cogs.audit_logger import AuditLogger
from src.kennybot.cogs.message_logger import MessageLogger
from src.kennybot.cogs.reaction_roles import ReactionRoles
from src.kennybot.cogs.slash_commands import SlashCommands
from src.kennybot.cogs.weekly_ai_posts import WeeklyAiPosts
from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.message_logger import log_codex_repair_mode
from src.kennybot.utils.text import sanitize_user_visible_error
from src.kennybot.utils.voice_recv_patch import apply_voice_recv_resilience_patch
from src.kennybot.utils.logger import install_asyncio_exception_handler
from src.kennybot.container import AppContainer, build_app_container


logger = logging.getLogger(__name__)
apply_voice_recv_resilience_patch()


class MyBot(commands.Bot):
    """Discord Bot メインクラス"""

    def __init__(self, *args, **kwargs):
        container = kwargs.pop("container", None)
        # 既定helpを無効化してカスタムhelpを使用
        kwargs.setdefault("help_command", None)
        super().__init__(*args, **kwargs)

        self.container: AppContainer = container or build_app_container()
        self.spam_guard = self.container.spam_guard
        self.meeting_minutes = self.container.meeting_minutes
        self.ai_progress_tracker = self.container.ai_progress_tracker
        self._recent_event_errors: dict[tuple[str, str], float] = {}
        self.chat_memory = self.container.chat_memory
        self.chat_service = self.container.chat_service
        self.ai_search = self.container.ai_search
        self.ollama_client = self.container.ollama_client
        self.ollama_embed_client = self.container.ollama_embed_client
        self.openai_vision_client = self.container.openai_vision_client
        self.openai_image_client = self.container.openai_image_client
        self.gemini_vision_client = self.container.gemini_vision_client
        self.gemini_image_client = self.container.gemini_image_client
        self.ollama_model = self.container.ollama_model
        self._tree_synced = False

    async def setup_hook(self):
        """Bot セットアップ（Cog登録）"""
        install_asyncio_exception_handler(asyncio.get_running_loop())
        self.tree.on_error = self.on_app_command_error
        await self.add_cog(VoiceLogger(self))
        await self.add_cog(MemberLogger(self))
        await self.add_cog(AuditLogger(self))
        await self.add_cog(MessageLogger(self))
        await self.add_cog(BirthdayReminders(self))
        await self.add_cog(WeeklyAiPosts(self))
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
                        self.tree.clear_commands(guild=guild)
                        guild_synced = await self.tree.sync(guild=guild)
                        logger.info("Guild slash commands cleared: guild=%s count=%d", guild.id, len(guild_synced))
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
        safe_error = sanitize_user_visible_error(error, max_chars=1000)
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
                ("エラー", safe_error, False),
            ],
        )
        text = "コマンド実行に失敗しました。詳細はログを確認してください。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            logger.exception("Failed to send global slash command error response")

    async def on_error(self, event_method: str, *args, **kwargs):
        tb = traceback.format_exc(limit=8)
        safe_tb = sanitize_user_visible_error(tb, max_chars=1000)
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
                ("例外", safe_tb, False),
            ],
        )
