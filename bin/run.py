# bin/run.py
# メイン実行エントリポイント
# - .env ファイルから環境変数を読み込む
# - DISCORD_TOKEN の取得
# - MyBot のインスタンス化
# - bot.run()

import sys
import logging
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加（絶対インポート対応）
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.kennybot.utils.env import load_env_file, require_env
from src.kennybot.utils.logger import setup_logging
from src.kennybot.utils.single_instance import SingleInstanceError, acquire_lock
from src.kennybot.bootstrap import create_bot
from src.kennybot.utils.message_logger import log_codex_repair_mode


def main():
    """Discord Bot メイン実行"""
    setup_logging()
    logger = logging.getLogger("kennybot.bootstrap")
    lock_path = Path("data") / "kennybot.lock"
    lock_retry_delay = 2
    waited_seconds = 0
    while True:
        try:
            acquire_lock(lock_path)
            break
        except SingleInstanceError as exc:
            waited_seconds += lock_retry_delay
            logger.warning(
                "Another kennybot instance is already running, waiting %ss: %s",
                waited_seconds,
                exc,
            )
            print(
                f"[BOOT] Another kennybot instance is already running, waiting {waited_seconds}s: {exc}",
                file=sys.stderr,
            )
            time.sleep(lock_retry_delay)

    # .env ファイルを読み込む
    load_env_file()

    # 必須環境変数を取得
    env_vars = require_env("DISCORD_TOKEN")
    token = env_vars["DISCORD_TOKEN"]

    bot = create_bot()
    try:
        bot.run(token)
    except Exception as exc:
        log_codex_repair_mode(
            trigger="startup_error",
            issue=str(exc),
            planned_fix="Discord 起動時の接続・認証・同期失敗を確認し、再起動や接続先の安定性を見直す",
            target_area="bot startup",
            level="error",
        )
        raise


if __name__ == "__main__":
    main()
