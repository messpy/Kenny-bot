"""Kenny Bot の起動ヘルパー。

現状は root 直下の実装を利用しつつ、起動経路を src 配下に寄せる。
将来的に機能ごとにリポジトリ分割する際は、ここを起点に差し替える。
"""

from __future__ import annotations

import discord

from src.kennybot.bot import MyBot


def create_bot() -> MyBot:
    """Discord Bot インスタンスを生成する。"""
    intents = discord.Intents.all()
    return MyBot(command_prefix="!", intents=intents)
