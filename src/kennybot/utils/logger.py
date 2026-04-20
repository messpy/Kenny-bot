# utils/logger.py
# ロギング初期化

import sys
import logging
from .paths import LOG_FILE


def setup_logging():
    """ロギング設定を初期化"""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def get_logger(name: str) -> logging.Logger:
    """ロガーを取得"""
    return logging.getLogger(name)
