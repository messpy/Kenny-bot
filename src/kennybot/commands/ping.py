# commands/ping.py
# （既存の ping.py から移動）

from discord.ext import commands


class PingCog(commands.Cog):
    """Ping コマンド"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        latency_ms = round(self.bot.latency * 1000)
        await ctx.send(f"Pong! {latency_ms}ms")
