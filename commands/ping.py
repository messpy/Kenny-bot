# commands/ping.py
# （既存の ping.py から移動）

from discord.ext import commands


class PingCog(commands.Cog):
    """Ping コマンド例"""

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        """Bot の応答速度を確認"""
        # 【実装例】
        # latency を表示
        pass
