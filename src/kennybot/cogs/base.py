# cogs/base.py
# ベース Cog クラス

from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands


class BaseCog(commands.Cog):
    """全 Cog の基底クラス"""

    JST = timezone(timedelta(hours=9))

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    def now(self) -> str:
        """現在時刻を JST 文字列で取得"""
        return datetime.now(self.JST).strftime("%Y/%m/%d %H:%M:%S")
