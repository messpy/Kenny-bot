# guards/mod_actions.py
# モデレーション処罰実行（削除、タイムアウト、キック、バン）

import logging
from dataclasses import dataclass
from datetime import timedelta

import discord
from discord.ext import commands

from utils.event_logger import send_event_log

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """処罰実行結果"""
    success: bool
    action: str
    detail: str = ""


class ModActions:
    """モデレーション処罰を実行"""

    @staticmethod
    async def delete_message(
        message: discord.Message,
        reason: str = "スパム"
    ) -> bool:
        """メッセージを削除

        Returns:
            成功時 True
        """
        try:
            await message.delete()
            logger.info(f"Deleted message {message.id} from {message.author}: {reason}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            return False

    @staticmethod
    async def timeout_user(
        member: discord.Member,
        duration_minutes: int = 10,
        reason: str = "スパム"
    ) -> ActionResult:
        """ユーザーをタイムアウト（ミュート）

        Args:
            member: 対象メンバー
            duration_minutes: ミュート期間（分）
            reason: 理由

        Returns:
            成功時 True
        """
        try:
            until = discord.utils.utcnow() + timedelta(minutes=duration_minutes)
            await member.timeout(until, reason=reason)
            logger.info(f"Timed out {member} for {duration_minutes} min: {reason}")
            return ActionResult(True, "mute", f"{duration_minutes}分タイムアウト")
        except discord.Forbidden:
            detail = "Bot に『メンバーをタイムアウト』権限がないか、ロール階層が対象より下です。"
            logger.error(f"Failed to timeout {member}: {detail}")
            return ActionResult(False, "mute", detail)
        except Exception as e:
            logger.error(f"Failed to timeout user: {e}")
            return ActionResult(False, "mute", str(e))

    @staticmethod
    async def kick_user(
        member: discord.Member,
        reason: str = "スパム"
    ) -> ActionResult:
        """ユーザーをキック

        Returns:
            成功時 True
        """
        try:
            await member.kick(reason=reason)
            logger.info(f"Kicked {member}: {reason}")
            return ActionResult(True, "kick", "")
        except discord.Forbidden:
            detail = "Bot に『メンバーをキック』権限がないか、ロール階層が対象より下です。"
            logger.error(f"Failed to kick {member}: {detail}")
            return ActionResult(False, "kick", detail)
        except Exception as e:
            logger.error(f"Failed to kick user: {e}")
            return ActionResult(False, "kick", str(e))

    @staticmethod
    async def ban_user(
        guild: discord.Guild,
        user: discord.User,
        reason: str = "スパム"
    ) -> ActionResult:
        """ユーザーをバン

        Returns:
            成功時 True
        """
        try:
            await guild.ban(user, reason=reason)
            logger.info(f"Banned {user}: {reason}")
            return ActionResult(True, "ban", "")
        except discord.Forbidden:
            detail = "Bot に『メンバーをBAN』権限がないか、ロール階層が対象より下です。"
            logger.error(f"Failed to ban {user}: {detail}")
            return ActionResult(False, "ban", detail)
        except Exception as e:
            logger.error(f"Failed to ban user: {e}")
            return ActionResult(False, "ban", str(e))

    @staticmethod
    def _resolve_bot_member(bot: commands.Bot, guild: discord.Guild) -> discord.Member | None:
        """ギルド内のBotメンバーを取得"""
        if guild.me:
            return guild.me
        if bot.user:
            return guild.get_member(bot.user.id)
        return None

    @staticmethod
    def _validate_target(bot_member: discord.Member | None, member: discord.Member) -> str | None:
        """処罰可能な対象かを判定"""
        if bot_member is None:
            return "Bot自身のメンバー情報を取得できません。"
        if member.guild.owner_id == member.id:
            return "サーバーオーナーは処罰できません。"
        if member.id == bot_member.id:
            return "Bot自身は処罰できません。"
        if member.top_role >= bot_member.top_role:
            return "Botロールが対象ユーザー以下です。Botのロールを対象より上にしてください。"
        return None

    @staticmethod
    async def execute_level(
        bot: commands.Bot,
        guild: discord.Guild,
        member: discord.Member,
        level: str
    ) -> ActionResult:
        """違反レベルに応じた処罰を実行

        Args:
            bot: Bot インスタンス
            guild: ギルド
            member: 対象メンバー
            level: 違反レベル (warning|mute|kick|ban)

        Returns:
            成功時 True
        """
        if level == "warning":
            # 警告は cogs で送出する想定
            logger.info(f"Warning for {member}")
            await send_event_log(
                bot,
                guild=guild,
                level="warning",
                title="モデレーション警告",
                description="警告レベルの違反を記録しました。",
                fields=[
                    ("対象ユーザー", f"{member} ({member.id})", False),
                    ("アクション", "warning", True),
                ],
            )
            return ActionResult(True, "warning", "")

        bot_member = ModActions._resolve_bot_member(bot, guild)
        blocked_reason = ModActions._validate_target(bot_member, member)
        if blocked_reason:
            await send_event_log(
                bot,
                guild=guild,
                level="warning",
                title="モデレーション失敗",
                description="対象ユーザーを処罰できませんでした。",
                fields=[
                    ("対象ユーザー", f"{member} ({member.id})", False),
                    ("アクション", level, True),
                    ("理由", blocked_reason[:1000], False),
                ],
            )
            return ActionResult(False, level, blocked_reason)

        assert bot_member is not None
        if level == "mute":
            if not bot_member.guild_permissions.moderate_members:
                result = ActionResult(False, "mute", "Botに『メンバーをタイムアウト』権限がありません。")
            else:
                result = await ModActions.timeout_user(member, duration_minutes=30, reason="スパム違反")
            await send_event_log(
                bot,
                guild=guild,
                level="success" if result.success else "error",
                title="モデレーション実行",
                description="タイムアウト処理を実行しました。",
                fields=[
                    ("対象ユーザー", f"{member} ({member.id})", False),
                    ("アクション", result.action, True),
                    ("結果", "成功" if result.success else "失敗", True),
                    ("詳細", (result.detail or "-")[:1000], False),
                ],
            )
            return result
        if level == "kick":
            if not bot_member.guild_permissions.kick_members:
                result = ActionResult(False, "kick", "Botに『メンバーをキック』権限がありません。")
            else:
                result = await ModActions.kick_user(member, reason="スパム違反")
            await send_event_log(
                bot,
                guild=guild,
                level="success" if result.success else "error",
                title="モデレーション実行",
                description="キック処理を実行しました。",
                fields=[
                    ("対象ユーザー", f"{member} ({member.id})", False),
                    ("アクション", result.action, True),
                    ("結果", "成功" if result.success else "失敗", True),
                    ("詳細", (result.detail or "-")[:1000], False),
                ],
            )
            return result
        if level == "ban":
            if bot_member.guild_permissions.ban_members:
                ban_result = await ModActions.ban_user(guild, member, reason="スパム違反")
                if ban_result.success:
                    await send_event_log(
                        bot,
                        guild=guild,
                        level="success",
                        title="モデレーション実行",
                        description="BAN 処理を実行しました。",
                        fields=[
                            ("対象ユーザー", f"{member} ({member.id})", False),
                            ("アクション", ban_result.action, True),
                            ("結果", "成功", True),
                            ("詳細", (ban_result.detail or "-")[:1000], False),
                        ],
                    )
                    return ban_result

            # BAN不可/失敗時のフォールバック: 可能なら kick
            if bot_member.guild_permissions.kick_members:
                kick_result = await ModActions.kick_user(
                    member,
                    reason="スパム違反（BAN失敗のためKICKにフォールバック）"
                )
                if kick_result.success:
                    kick_result.action = "kick (ban失敗フォールバック)"
                    if not kick_result.detail:
                        kick_result.detail = "BANに失敗したためKICKで退室させました。"
                await send_event_log(
                    bot,
                    guild=guild,
                    level="success" if kick_result.success else "error",
                    title="モデレーション実行",
                    description="BAN の代替として KICK を実行しました。",
                    fields=[
                        ("対象ユーザー", f"{member} ({member.id})", False),
                        ("アクション", kick_result.action, True),
                        ("結果", "成功" if kick_result.success else "失敗", True),
                        ("詳細", (kick_result.detail or "-")[:1000], False),
                    ],
                )
                return kick_result

            result = ActionResult(
                False,
                "ban",
                "BAN権限がないかBANに失敗し、KICK権限もないため追放できませんでした。"
            )
            await send_event_log(
                bot,
                guild=guild,
                level="error",
                title="モデレーション実行",
                description="BAN 処理に失敗しました。",
                fields=[
                    ("対象ユーザー", f"{member} ({member.id})", False),
                    ("アクション", result.action, True),
                    ("結果", "失敗", True),
                    ("詳細", result.detail[:1000], False),
                ],
            )
            return result
        return ActionResult(False, level, "不明な違反レベルです。")
