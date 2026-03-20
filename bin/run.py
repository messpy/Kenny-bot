# bin/run.py
# メイン実行エントリポイント
# - .env ファイルから環境変数を読み込む
# - DISCORD_TOKEN の取得
# - MyBot のインスタンス化
# - bot.run()

import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加（絶対インポート対応）
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.env import load_env_file, require_env
from utils.single_instance import SingleInstanceError, acquire_lock
from bot import MyBot


def main():
    """Discord Bot メイン実行"""
    try:
        acquire_lock(Path("data") / "kennybot.lock")
    except SingleInstanceError as exc:
        print(f"[BOOT] Another kennybot instance is already running: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # .env ファイルを読み込む
    load_env_file()

    # 必須環境変数を取得
    env_vars = require_env("DISCORD_TOKEN")
    token = env_vars["DISCORD_TOKEN"]

    import discord
    intents = discord.Intents.all()
    bot = MyBot(command_prefix="!", intents=intents)
    bot.run(token)


if __name__ == "__main__":
    main()
