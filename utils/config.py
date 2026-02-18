# utils/config.py
# チャンネル名、ログパス、定数などの集約

import sys
from pathlib import Path
from datetime import datetime

# =========================
# ログ設定
# =========================
LOG_DIR = Path("log")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / datetime.now().strftime("kennybot_%Y%m%d.log")

# メッセージ履歴保存先
MESSAGE_LOG_DIR = Path("data") / "message_logs"
MESSAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# チャンネル名（固定）
# =========================
CHANNEL_NAME_MEMBER_EVENTS = "member-events"   # メンバー参加/退出
CHANNEL_NAME_VOICE_EVENTS = "voice-events"     # VC 入退室
CHANNEL_NAME_OTHER_LOG = "other-log"           # その他ログ（任意）
CHANNEL_NAME_BOT_LOG = "bot-log"               # Bot 自体の状態など（任意）

CHANNEL_NAMES = {
    "member": CHANNEL_NAME_MEMBER_EVENTS,
    "voice": CHANNEL_NAME_VOICE_EVENTS,
    "other": CHANNEL_NAME_OTHER_LOG,
    "bot": CHANNEL_NAME_BOT_LOG,
}

# =========================
# Ollama モデル設定（統一管理）
# =========================
OLLAMA_MODEL_DEFAULT = "gpt-oss:120b-cloud"
OLLAMA_MODEL_CHAT = "gpt-oss:120b-cloud"
OLLAMA_MODEL_SUMMARY = "gpt-oss:120b-cloud"
OLLAMA_TIMEOUT_SEC = 180

# =========================
# メッセージ処理設定
# =========================
# 会話履歴の参照範囲（何件前までのメッセージを考慮するか）
# より多いと文脈が豊か、少ないと軽量。推奨: 10-15
CHAT_HISTORY_LINES = 10

# AI 応答の最大文字数（Discord 文字制限は 2000 文字、メンション部を乗せると急简でいいので 1800 明値に）
# この長さを超える場合は "...(省略)..." で切る
MAX_RESPONSE_LENGTH = 1800
MAX_RESPONSE_LENGTH_PROMPT = 500

# キーワードリアクション設定
KEYWORD_REACTIONS = {
    "いいね": "👍",
    "ミュ": "🐈️",
    "みゅ": "🐈️",
    "草": "😂",
    "天才": "🧠",
    "かわいい": "💕",
    "おはよう": "☀",
    "おやすみ": "🌙",
    "天使": "て、て、て、天使の羽👼",
}

# ユーザーあだな設定（ユーザーID: あだな）
# 例: 123456789: "バナナ"
USER_NICKNAMES = {
    # ここにあだなマッピングを追加
    # 形式: user_id: "nickname"
}

# プロンプトテンプレート
PROMPT_TEMPLATE = (
    "以下は Discord ユーザー「{user_display}」からのメッセージです。\n"
    "日本語でフレンドリーに、親しみやすく回答してください。\n"
    "回答は {max_response_length_prompt} 文字以内で返す。\n"
    "{history_context}"
    "メッセージ:\n{user_message}"
)

HISTORY_CONTEXT_TEMPLATE = (
    "[このユーザーの最近の行動]\n"
    "{history}\n\n"
)

# =========================
# モデレーション設定
# =========================
# モデレーションパネルチャンネルID
MOD_PANEL_CHANNEL_ID = 1005826751391342663

# スパム処罰の段階
# violation_count に基づて自動昇格:
# 1 達成: warning
# 2-3 達成: mute (30分)
# 4 達成: kick
# 5+ 達成: ban
VIOLATION_LEVELS = {
    "warning": "警告",
    "mute": "タイムアウト (30分)",
    "kick": "キック",
    "ban": "バン",
}
