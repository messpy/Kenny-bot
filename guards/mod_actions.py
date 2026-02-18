# guards/mod_actions.py
# モデレーション処罰実行（削除、タイムアウト、キック、バン）

import logging
from typing import Optional
from datetime import datetime, timedelta

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


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
    ) -> bool:
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
            return True
        except discord.Forbidden:
            logger.error(
                f"Failed to timeout {member}: Bot に 'メンバーをタイムアウト' 権限がありません。"
                f"Bot の役割がターゲットユーザーより上位にあることを確認してください。"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to timeout user: {e}")
            return False

    @staticmethod
    async def kick_user(
        member: discord.Member,
        reason: str = "スパム"
    ) -> bool:
        """ユーザーをキック

        Returns:
            成功時 True
        """
        try:
            await member.kick(reason=reason)
            logger.info(f"Kicked {member}: {reason}")
            return True
        except discord.Forbidden:
            logger.error(
                f"Failed to kick {member}: Bot に 'メンバーをキック' 権限がありません。"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to kick user: {e}")
            return False

    @staticmethod
    async def ban_user(
        guild: discord.Guild,
        user: discord.User,
        reason: str = "スパム"
    ) -> bool:
        """ユーザーをバン

        Returns:
            成功時 True
        """
        try:
            await guild.ban(user, reason=reason)
            logger.info(f"Banned {user}: {reason}")
            return True
        except discord.Forbidden:
            logger.error(
                f"Failed to ban {user}: Bot に 'メンバーをバン' 権限がありません。"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to ban user: {e}")
            return False

    @staticmethod
    async def execute_level(
        bot: commands.Bot,
        guild: discord.Guild,
        member: discord.Member,
        level: str
    ) -> bool:
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
            return True
        elif level == "mute":
            return await ModActions.timeout_user(member, duration_minutes=30, reason="スパム違反")
        elif level == "kick":
            return await ModActions.kick_user(member, reason="スパム違反")
        elif level == "ban":
            return await ModActions.ban_user(guild, member, reason="スパム違反")
        return False
