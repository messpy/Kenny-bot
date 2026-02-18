# 🤖 Discord Bot - Kenny Bot（リファクタリド版）

モジュール化されたアーキテクチャを備えた高機能な Discord Bot です。
AI との会話、スパム管理、ユーザー履歴追跡などの機能を備えています。

## 📋 主な機能

### 1. **AI 会話機能**
- ✅ メンション・リプライに対して Ollama を使用した AI 応答
- ✅ ユーザーの過去 10 件のメッセージから会話文脈を自動構築
- ✅ ユーザー名とあだなによるパーソナライズ応答
- ✅ 時刻付きメッセージ履歴で「いつ何をしたか」を記憶

### 2. **スパム管理システム**
- ✅ 自動スパム検出（連投、重複メッセージ、高頻度 AI 呼び出し）
- ✅ 段階的な違反レベル管理（警告 → タイムアウト → キック → バン）
- ✅ 自動メッセージ削除と処罰実行
- ✅ リアクションによる違反リセット機能

### 3. **メッセージ機能**
- ✅ キーワード自動リアクション
- ✅ メッセージ履歴の JSON 保存（チャンネル単位）
- ✅ ユーザー ID による個人識別（同名ユーザーも区別可能）

### 4. **モデレーション**
- ✅ 違反者管理パネル（モデレーションチャンネル内）
- ✅ リアクション `🔄` で違反リセット
- ✅ リアクション `📋` で違反一覧表示

## 📁 ディレクトリ構造

```
project_refactored/
├── bin/
│   ├── __init__.py
│   └── run.py                    # エントリーポイント
├── utils/
│   ├── __init__.py
│   ├── config.py                 # 定数・設定（一元管理）
│   ├── env.py                    # 環境変数管理
│   ├── logger.py                 # ロギング設定
│   ├── channel.py                # チャンネル解決
│   ├── text.py                   # テキスト処理
│   └── message_store.py          # メッセージ履歴管理
├── guards/
│   ├── __init__.py
│   ├── spam_guard.py             # スパム検出・違反レベル管理
│   └── mod_actions.py            # 処罰実行（削除、タイムアウト、キック、バン）
├── ai/
│   ├── __init__.py
│   ├── runner.py                 # Ollama 実行（subprocess ベース）
│   ├── chat.py                   # 会話メモリ・サービス
│   ├── client.py                 # Ollama Client API（HTTP ベース）
│   └── search.py                 # Web 検索・要約
├── cogs/
│   ├── __init__.py
│   ├── base.py                   # BaseCog（ユーティリティ）
│   ├── voice_logger.py           # VC イベント
│   ├── member_logger.py          # メンバーイベント
│   ├── message_logger.py         # メッセージ＆会話＆スパム
│   └── mod_panel.py              # モデレーションパネル
├── commands/
│   ├── __init__.py
│   ├── ping.py
│   └── action_commands.py
├── bot.py                        # Bot メインクラス
├── pyproject.toml                # 依存関係定義
├── .env.example                  # 環境変数テンプレート
└── README.md                     # このファイル
```

## 🚀 セットアップ

### 1. 前提条件
- Python 3.13+
- Ollama（ローカルまたはリモート）
- uv（Python パッケージマネージャ）

### 2. リポジトリクローン
```bash
cd project_refactored
```

### 3. 依存関係のインストール
```bash
uv sync
```

### 4. 環境変数の設定
```bash
# .env.example をコピー
cp .env.example .env

# .env を編集（DISCORD_TOKEN は必須）
```

**必須環境変数：**
```env
DISCORD_TOKEN=your_discord_bot_token_here
```

**オプション環境変数：**
```env
# リモート Ollama の場合
OLLAMA_HOST=https://ollama.example.com
OLLAMA_API_KEY=your_api_key_here
```

### 5. Ollama のセットアップ
```bash
# ローカル Ollama（推奨モデル: gpt-oss:120b-cloud）
ollama pull gpt-oss:120b-cloud
ollama serve

# 別ターミナルで Bot を起動
python bin/run.py
```

## ⚙️ 設定方法

### モデル設定（config.py）

```python
# utils/config.py
OLLAMA_MODEL_DEFAULT = "gpt-oss:120b-cloud"
OLLAMA_MODEL_CHAT = "gpt-oss:120b-cloud"
OLLAMA_MODEL_SUMMARY = "gpt-oss:120b-cloud"
```

### キーワードリアクション

```python
# utils/config.py
KEYWORD_REACTIONS = {
    "いいね": "👍",
    "草": "😂",
    "天才": "🧠",
    "かわいい": "💕",
    # 自由に追加可能
}
```

### ユーザーあだな

```python
# utils/config.py - 30% の確率で使用される
USER_NICKNAMES = {
    123456789: "バナナ",
    987654321: "ちゃん",
}
```

### 会話履歴参照行数

```python
# utils/config.py - プロンプトに含まれるメッセージ数
CHAT_HISTORY_LINES = 10
```

### モデレーションパネルチャンネル

```python
# utils/config.py
MOD_PANEL_CHANNEL_ID = 1005826751391342663
```

## 📖 使用方法

### AI 会話機能

Bot にメンション or リプライすると自動応答：

```
@Kenny おはよう！
→ Bot: おはようございます、Kenny さん！今日はいい天気ですね。
```

**特徴：**
- ✅ ユーザーの過去 10 件のメッセージから文脈を取得
- ✅ 時刻付き履歴を含める（「xx時にこんなこと言ってた」を記憶）
- ✅ 同じサーバー内でユーザー ID で個人を識別

### キーワード自動リアクション

メッセージ内で設定されたキーワードを検出して絵文字反応：

```
ユーザー: これ天才だ！
→ Bot: 🧠 を自動追加
```

### スパム検出・自動処罰

**検出対象：**
- 短時間の連投（5 メッセージ / 8 秒）
- 同一文の重複（12 秒以内）
- AI 呼び出しの過度な利用（2 回 / 20 秒）

**段階的処罰：**
1. **1 回目**: ⚠️ 警告メッセージ
2. **2-3 回目**: 🔇 タイムアウト（30 分）
3. **4 回目**: 🚫 キック
4. **5 回目以上**: 🔨 バン

### モデレーションパネル

**パネルの作成（管理者のみ）**：
```bash
/modpanel
```

**リアクション操作：**
- **🔄**: 指定ユーザーの違反をリセット
- **📋**: サーバー内の全違反ユーザーを表示

**使用例：**
```
チャンネル #mod-panel
│
├─ [Bot メッセージ]
│  ┌─────────────────────────────────────────┐
│  │ 🛡️ スパム管理パネル                     │
│  │                                         │
│  │ ユーザーID: 123456789                   │
│  │ レベル: mute                            │
│  │ 違反回数: 2                             │
│  └─────────────────────────────────────────┘
│  🔄 [リセット] 📋 [一覧表示]
│
└─ (Admin が 🔄 をクリック)
   → "✅ ユーザーID 123456789 の違反をリセットしました。"
```

## 📊 メッセージ履歴の仕様

### 保存形式
```json
{
  "id": 123456789,
  "author_id": 987654321,
  "author": "Kenny",
  "content": "こんにちは",
  "timestamp": "2026-02-18T10:30:00+09:00"
}
```

### 保存方式
- **場所**: `data/message_logs/guild_{id}_channel_{id}.json`
- **保持件数**: チャンネル単位で最新 1000 件
- **更新頻度**: リアルタイム（全メッセージ）

### 履歴の活用
- **AI プロンプト**: 過去 10 件を文脈として含める
- **個人識別**: ユーザー ID で同名ユーザーも区別

## 🔧 トラブルシューティング

### モデルが見つからないエラー
```
Error: model 'tinyllama' not found
```

**解決：**
```bash
# モデルをインストール
ollama pull gpt-oss:120b-cloud

# config.py で正しいモデル名を設定
OLLAMA_MODEL_DEFAULT = "gpt-oss:120b-cloud"
```

### 認証エラー（リモート Ollama）
```
Error: 401 Unauthorized
```

**解決：**
```bash
# .env を確認
echo $OLLAMA_API_KEY

# または設定
export OLLAMA_API_KEY=your_api_key
```

### メッセージ送信権限エラー
Bot にメッセージ送信・削除権限があることを確認してください。

**必要な権限：**
- ✅ メッセージを送信
- ✅ リアクションを追加
- ✅ メッセージを管理
- ✅ メンバーをタイムアウト
- ✅ メンバーをキック
- ✅ メンバーをバン

## 👨‍💻 開発者向けガイド

### 新しい Cog を追加

```python
# cogs/my_cog.py
import discord
from discord.ext import commands
from cogs.base import BaseCog

class MyCog(BaseCog):
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # 処理
        pass

async def setup(bot: commands.Bot):
    await bot.add_cog(MyCog(bot))
```

### bot.py に登録
```python
async def setup_hook(self):
    await self.add_cog(MyCog(self))
```

### メッセージ履歴を取得

```python
from utils.message_store import MessageStore

store = MessageStore(guild_id, channel_id)
context = store.get_recent_context(lines=10)
print(context)
```

### スパムガードを操作

```python
spam_guard = self.bot.spam_guard

# 違反を追加
violation = spam_guard.add_violation(user_id, guild_id)
print(f"Current level: {violation.current_level}")

# 違反をリセット
spam_guard.reset_violation(user_id, guild_id)
```

## 📝 ロギング

ログは `log/` ディレクトリに保存されます：

```bash
log/
└── kennybot_20260218.log
```

**主要なログレベル：**
- `INFO`: 正常な動作
- `WARNING`: 警告
- `ERROR`: エラー
- `DEBUG`: 詳細情報

## 📄 ライセンス

MIT License

## 👥 貢献

プルリクエストを歓迎します！

---

**最終更新**: 2026-02-18
