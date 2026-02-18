# cogs/voice_logger.py
# VC ログ（Embed 形式 / 通話時間付き）

from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional

import discord
from discord.ext import commands

JST = timezone(timedelta(hours=9))


class VoiceLogger(commands.Cog):
    """VC 入退室ログ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ユーザーごとの入室時刻を記録: (user_id, guild_id) -> datetime
        self._voice_join_times: Dict[Tuple[int, int], datetime] = {}

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """VC の参加・離脱・移動を監視"""
        guild = member.guild

        # 入室した場合
        if before.channel is None and after.channel is not None:
            await self._handle_voice_join(member, after.channel, guild)

        # 退出した場合
        elif before.channel is not None and after.channel is None:
            await self._handle_voice_leave(member, before.channel, guild)

        # チャンネル変更した場合（退出+入室として扱う）
        elif before.channel != after.channel:
            await self._handle_voice_leave(member, before.channel, guild)
            await self._handle_voice_join(member, after.channel, guild)

    async def _handle_voice_join(self, member: discord.Member, channel: discord.VoiceChannel, guild: discord.Guild):
        """VC入室を記録してロギング"""
        # 入室時刻を記録
        self._voice_join_times[(member.id, guild.id)] = datetime.now(JST)

        # voice-events チャンネルを取得
        log_channel = discord.utils.get(guild.text_channels, name="voice-events")
        if not log_channel:
            return

        # Embed を生成
        embed = discord.Embed(
            title="VC入室",
            description=f"{member.mention} が ⁠{channel.name} に入室しました",
            color=discord.Color.green(),
            timestamp=datetime.now(JST)
        )
        embed.add_field(name="ユーザー", value=f"{member.name} ({member.id})", inline=False)
        embed.add_field(name="サーバー", value=f"{guild.name} ({guild.id})", inline=False)
        embed.add_field(name="チャンネル", value=f"{channel.name}", inline=False)

        await log_channel.send(embed=embed)

    async def _handle_voice_leave(self, member: discord.Member, channel: discord.VoiceChannel, guild: discord.Guild):
        """VC離脱を記録してロギング"""
        # 入室時刻を取得
        join_time = self._voice_join_times.pop((member.id, guild.id), None)
        duration = self._calculate_duration(join_time) if join_time else "不明"

        # voice-events チャンネルを取得
        log_channel = discord.utils.get(guild.text_channels, name="voice-events")
        if not log_channel:
            return

        # Embed を生成
        embed = discord.Embed(
            title="VC離脱",
            description=f"{member.mention} が ⁠{channel.name} から離脱しました",
            color=discord.Color.red(),
            timestamp=datetime.now(JST)
        )
        embed.add_field(name="ユーザー", value=f"{member.name} ({member.id})", inline=False)
        embed.add_field(name="サーバー", value=f"{guild.name} ({guild.id})", inline=False)
        embed.add_field(name="チャンネル", value=f"{channel.name}", inline=False)
        embed.add_field(name="通話時間", value=duration, inline=False)

        await log_channel.send(embed=embed)

    def _calculate_duration(self, join_time: Optional[datetime]) -> str:
        """入室時刻から通話時間を計算"""
        if not join_time:
            return "不明"

        duration = datetime.now(JST) - join_time
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours}:{minutes:02d}:{seconds:02d}"
