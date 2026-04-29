# cogs/member_logger.py
# Member join/leave ログ

import discord
from discord.ext import commands

from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.time import now_jst, as_jst


class MemberLogger(commands.Cog):
    """メンバー参加・退出ログ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """メンバー参加時"""
        guild = member.guild

        # アカウント作成日を取得
        account_age = now_jst() - as_jst(member.created_at)
        age_str = f"{account_age.days}日"
        await send_event_log(
            self.bot,
            guild=guild,
            level="success",
            title="メンバー参加",
            description=f"{member.mention} がサーバーに参加しました",
            fields=[
                ("ユーザー", f"{member.name} ({member.id})", False),
                ("サーバー", f"{guild.name} ({guild.id})", False),
                ("アカウント作成日", f"{member.created_at.strftime('%Y/%m/%d %H:%M')} (約{age_str}前)", False),
            ],
            channel_kind="member",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """メンバー退出時"""
        guild = member.guild
        await send_event_log(
            self.bot,
            guild=guild,
            level="warning",
            title="メンバー退出",
            description=f"{member.mention} がサーバーを退出しました",
            fields=[
                ("ユーザー", f"{member.name} ({member.id})", False),
                ("サーバー", f"{guild.name} ({guild.id})", False),
            ],
            channel_kind="member",
        )
