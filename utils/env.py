# utils/env.py
# 環境変数管理（.env ファイルサポート）

import os
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("python-dotenv が必要です。pip install python-dotenv を実行してください。")


def load_env_file(dotenv_path: Optional[str] = None) -> None:
    """
    .env ファイルから環境変数を読み込む
    
    Args:
        dotenv_path: .env ファイルのパス。None の場合はデフォルト位置を探索
    """
    if dotenv_path:
        path = Path(dotenv_path)
    else:
        # デフォルト: project_refactored直下 と その親ディレクトリを探索
        default_paths = [
            Path(".env"),
            Path("../.env"),
            Path("../../.env"),
        ]
        path = None
        for p in default_paths:
            if p.exists():
                path = p
                break
    
    if path and path.exists():
        load_dotenv(path)
        print(f"[ENV] Loaded from {path.resolve()}")
    else:
        print(f"[ENV] No .env file found, using system environment variables")


def get_env(key: str, default: Optional[str] = None) -> str:
    """
    環境変数を取得（デフォルト値対応）
    
    Args:
        key: 環境変数名
        default: デフォルト値
    
    Returns:
        環境変数の値
    
    Raises:
        ValueError: key が見つからず default がない場合
    """
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"環境変数 {key} が設定されていません。")
    return value


def require_env(*keys: str) -> dict[str, str]:
    """
    必須環境変数を一括取得
    
    Args:
        keys: 環境変数名（複数）
    
    Returns:
        {key: value, ...} の辞書
    
    Raises:
        ValueError: いずれかの key が見つからない場合
    """
    result = {}
    missing = []
    for key in keys:
        value = os.getenv(key)
        if value is None:
            missing.append(key)
        else:
            result[key] = value
    
    if missing:
        raise ValueError(f"必須環境変数が見つかりません: {', '.join(missing)}")
    
    return result
