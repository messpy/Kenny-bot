# cogs/base.py
# ベース Cog クラス

import discord
from discord.ext import commands
from src.kennybot.utils.time import format_jst, now_jst


class BaseCog(commands.Cog):
    """全 Cog の基底クラス"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    def now(self) -> str:
        """現在時刻を JST 文字列で取得"""
        return format_jst(now_jst(), "%Y/%m/%d %H:%M:%S")
