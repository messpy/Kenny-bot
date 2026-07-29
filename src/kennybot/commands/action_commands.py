# commands/action_commands.py
# （既存の action_commands.py から移動）

import discord
from discord.ext import commands


class ActionCog(commands.Cog):
    """汎用アクションコマンド"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_info_embed(self) -> discord.Embed:
        guild_count = len(self.bot.guilds)
        member_count = 0
        for guild in self.bot.guilds:
            if guild.member_count:
                member_count += int(guild.member_count)

        embed = discord.Embed(
            title="Kenny Bot Action",
            description="`!action help` / `!action ping` / `!action info` を使えます。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Ping", value=f"{round(self.bot.latency * 1000, 1)} ms", inline=True)
        embed.add_field(name="参加サーバー", value=str(guild_count), inline=True)
        embed.add_field(name="総メンバー数(概算)", value=str(member_count), inline=True)
        return embed

    @commands.command(name="action")
    async def action(self, ctx: commands.Context, action: str | None = None):
        name = (action or "help").strip().lower()
        if name in {"help", "h", "?"}:
            await ctx.send("使い方: `!action ping` / `!action info` / `!action help`")
            return
        if name == "ping":
            latency_ms = round(self.bot.latency * 1000)
            await ctx.send(f"Pong! {latency_ms}ms")
            return
        if name in {"info", "status"}:
            await ctx.send(embed=self._build_info_embed())
            return
        await ctx.send("未知の action です。`!action help` を使ってください。")
