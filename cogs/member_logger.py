# cogs/member_logger.py
# Member join/leave ログ

from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

JST = timezone(timedelta(hours=9))


class MemberLogger(commands.Cog):
    """メンバー参加・退出ログ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """メンバー参加時"""
        guild = member.guild

        # member-events チャンネルを取得
        log_channel = discord.utils.get(guild.text_channels, name="member-events")
        if not log_channel:
            return

        # アカウント作成日を取得
        account_age = datetime.now(JST) - member.created_at.replace(tzinfo=JST)
        age_str = f"{account_age.days}日"

        # Embed を生成
        embed = discord.Embed(
            title="メンバー参加",
            description=f"{member.mention} がサーバーに参加しました",
            color=discord.Color.green(),
            timestamp=datetime.now(JST)
        )
        embed.add_field(name="ユーザー", value=f"{member.name} ({member.id})", inline=False)
        embed.add_field(name="サーバー", value=f"{guild.name} ({guild.id})", inline=False)
        embed.add_field(name="アカウント作成日", value=f"{member.created_at.strftime('%Y/%m/%d %H:%M')} (約{age_str}前)", inline=False)

        await log_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """メンバー退出時"""
        guild = member.guild

        # member-events チャンネルを取得
        log_channel = discord.utils.get(guild.text_channels, name="member-events")
        if not log_channel:
            return

        # Embed を生成
        embed = discord.Embed(
            title="メンバー退出",
            description=f"{member.mention} がサーバーを退出しました",
            color=discord.Color.red(),
            timestamp=datetime.now(JST)
        )
        embed.add_field(name="ユーザー", value=f"{member.name} ({member.id})", inline=False)
        embed.add_field(name="サーバー", value=f"{guild.name} ({guild.id})", inline=False)

        await log_channel.send(embed=embed)
