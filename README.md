# 🤖 Discord Bot - Kenny Bot（リファクタリド版）

モジュール化されたアーキテクチャを備えた高機能な Discord Bot です。
会話応答、スパム管理、ユーザー履歴追跡などの機能を備えています。

## 📋 主な機能

### 1. **会話機能**
- ✅ メンション・リプライに対して会話応答
- ✅ DM でもそのまま会話可能
- ✅ 本人履歴とチャンネル全体履歴を状況に応じて自動選択
- ✅ `ollama.embed()` を使った semantic memory により、意味的に近い過去発言も参照可能
- ✅ README と `data/chat_rag.md/json/toml` をローカル知識として参照可能
- ✅ リモート接続 + API キー構成では必要時のみ web search / web fetch を使用
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
- ✅ SQLite ベースのメッセージ embedding 保存
- ✅ ユーザー ID による個人識別（同名ユーザーも区別可能）

### 4. **モデレーション**
- ✅ 違反者管理パネル（モデレーションチャンネル内）
- ✅ リアクション `🔄` で違反リセット
- ✅ リアクション `📋` で違反一覧表示

### 5. **ゲーム/音声機能**
- ✅ 人狼役職配布
- ✅ 人狼は DM のリアクションで襲撃対象を選択
- ✅ あいうえおバトルは 1 人から開始可能
- ✅ あいうえおバトルのお題は DM で受付（ひらがなのみ・7文字以下）
- ✅ VOICEVOX 読み上げ
- ✅ 通話の文字起こし/要約
- ✅ 通話文字起こしは Google Speech-to-Text を優先し、失敗時は faster-whisper にフォールバック

## 📁 ディレクトリ構造

```
project_refactored/
├── bin/
│   ├── __init__.py
│   ├── run.py                    # エントリーポイント
│   ├── reindex_message_embeddings.py  # 履歴の embedding 再インデックス
│   └── start_voicevox.sh         # VOICEVOX 起動補助
├── utils/
│   ├── __init__.py
│   ├── config.py                 # 定数・設定（一元管理）
│   ├── env.py                    # 環境変数管理
│   ├── logger.py                 # ロギング設定
│   ├── channel.py                # チャンネル解決
│   ├── text.py                   # テキスト処理
│   ├── event_logger.py           # event log 送信
│   ├── runtime_settings.py       # 実行時設定
│   ├── command_catalog.py        # Slash command メタ情報
│   ├── countdown.py              # カウントダウン表示共通処理
│   ├── local_rag.py              # README / chat_rag.* のローカルRAG
│   ├── live_info.py              # 外部情報参照補助
│   ├── meeting_minutes.py        # 議事録管理
│   ├── message_store.py          # メッセージ履歴管理
│   ├── message_vector_store.py   # semantic memory 用 SQLite ストア
│   ├── build_info.py             # ビルド情報読み出し
│   ├── single_instance.py        # 多重起動防止
│   └── voice_recv_patch.py       # 音声受信まわりの補助
├── guards/
│   ├── __init__.py
│   ├── spam_guard.py             # スパム検出・違反レベル管理
│   └── mod_actions.py            # 処罰実行（削除、タイムアウト、キック、バン）
├── ai/
│   ├── __init__.py
│   ├── runner.py                 # Ollama 実行（subprocess ベース）
│   ├── chat.py                   # 会話メモリ・サービス
│   ├── client.py                 # Ollama Client API（HTTP ベース）
│   ├── search.py                 # Web 検索・要約
│   ├── google_speech.py          # Google Speech-to-Text
│   └── genimg.py                 # 画像生成補助
├── cogs/
│   ├── __init__.py
│   ├── base.py                   # BaseCog（ユーティリティ）
│   ├── audit_logger.py           # 監査ログ（リアクション / slash / 設定変更）
│   ├── voice_logger.py           # VC イベント
│   ├── member_logger.py          # メンバーイベント
│   ├── message_logger.py         # メッセージ＆会話＆スパム
│   ├── mod_panel.py              # モデレーションパネル
│   ├── slash_commands.py         # 主要 slash command 群
│   ├── game_commands.py          # ゲーム機能（人狼 / ワードウルフ等）
│   ├── reaction_roles.py         # リアクションロール
│   └── tts_reader.py             # VOICEVOX 読み上げ
├── commands/
│   ├── __init__.py
│   ├── ping.py
│   └── action_commands.py
├── data/
│   ├── bot_settings.yaml         # ランタイム設定
│   ├── build_info.json           # build 情報
│   ├── chat_rag.md               # 会話専用の追加RAG
│   ├── wordwolf_pairs.json       # ワードウルフ用語ペア
│   ├── message_logs/             # 会話履歴JSONとベクトルDB
│   └── meeting_audio_debug/      # 議事録デバッグ音声
├── systemd/
│   ├── kennybot.service          # Bot サービス unit
│   └── kennybot-voicevox.service # VOICEVOX 用 unit
├── bot.py                        # Bot メインクラス
├── docker-compose.yml            # Bot / Ollama / VOICEVOX 構成
├── Dockerfile                    # コンテナビルド定義
├── pyproject.toml                # 依存関係定義
├── uv.lock                       # lock file
├── .env.example                  # 環境変数テンプレート
└── README.md                     # このファイル
```

## 🚀 セットアップ

### Docker Compose でまとめて起動する方法

このリポジトリ単体では `Ollama` や `VOICEVOX` 本体は持っていません。自然な運用は `kennybot + Ollama + VOICEVOX Engine` を Docker Compose で一緒に立ち上げる形です。

```bash
cp .env.example .env
# .env に DISCORD_TOKEN を設定

docker compose up -d --build
```

この構成では `bot` から `ollama` と `voicevox` に Compose 内部ネットワークで接続するため、ホスト側ポートは公開しません。つまりホスト上の 11434 や 50021 と競合しません。

利用可能なモデル一覧は `/model_list`、切替は `/model_change` で確認・変更できます。

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

# ローカル Ollama で semantic memory を使う場合
# OLLAMA_HOST=http://127.0.0.1:11434

# Google Speech-to-Text を主系に使う場合
GOOGLE_SERVICE_ACCOUNT_JSON_BASE64=...
# または
# GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_CLOUD_PROJECT=your-project-id
```

### 5. Ollama のセットアップ
```bash
# リモート Ollama / cloud モデルを使う場合
# .env に OLLAMA_HOST と OLLAMA_API_KEY を設定
ollama serve

# 別ターミナルで Bot を起動
python bin/run.py
```

## ⚙️ 設定方法

### モデル設定（runtime settings）

```python
# data/bot_settings.yaml
OLLAMA_MODEL_DEFAULT = "gpt-oss:120b"
OLLAMA_MODEL_CHAT = "gpt-oss:120b"
OLLAMA_MODEL_SUMMARY = "gpt-oss:120b"
OLLAMA_MODEL_EMBEDDING = "embeddinggemma"
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

### 会話履歴・semantic memory

```python
# data/bot_settings.yaml
CHAT_HISTORY_LINES = 100
CHAT_USER_HISTORY_LINES = 24
CHAT_CHANNEL_HISTORY_LINES = 16
CHAT_SEMANTIC_HISTORY_K = 6
```

### モデレーションパネルチャンネル

```python
# utils/config.py
MOD_PANEL_CHANNEL_ID = 1005826751391342663
```

## 📖 使用方法

### 会話機能

Bot にメンション or リプライすると自動応答：

```
@Kenny おはよう！
→ Bot: おはようございます、Kenny さん！今日はいい天気ですね。
```

**特徴：**
- ✅ 本人履歴とチャンネル全体履歴を自動で使い分け
- ✅ semantic memory で意味的に近い過去発言も補助的に参照
- ✅ 時刻付き履歴を含める（「xx時にこんなこと言ってた」を記憶）
- ✅ 同じサーバー内でユーザー ID で個人を識別
- ✅ README や `data/chat_rag.md/json/toml` の内容を参照して Bot 自身の仕様説明に回答可能
- ✅ web search が使える構成では最新情報を検索して回答可能
- ✅ DM でも同様に会話可能

### 主なスラッシュコマンド

- `/help`: 利用できる機能とコマンド一覧を表示
- `/bot_info`: Bot の状態と疎通確認を表示
- `/summarize_recent`: チャンネルの直近メッセージを要約
- `/set_recent_window`: 要約の既定件数を設定
- `/config_show`: 設定値を表示
- `/config_set`: 設定値を更新
- `/model_list`: 利用可能なモデル一覧を表示（ローカル / リモート）
- `/model_change`: 利用モデルを切り替え（リモート接続時は `-cloud` モデル名を使用）
- `/minutes_start`: 議事録モードを開始
- `/minutes_stop`: 議事録モードを停止して要約を作成
- `/minutes_status`: 議事録モードの状態を表示
- `/reaction_role_set`: リアクションロール設定を追加
- `/reaction_role_remove`: リアクションロール設定を解除
- `/reaction_role_list`: リアクションロール設定を一覧表示
- `/tts_join`: 読み上げのため VC に参加
- `/tts_leave`: 読み上げを停止して VC から切断
- `/tts_voice`: 読み上げ話者 ID を変更
- `/tts_status`: 読み上げ状態を表示
- `/game`: ミニゲームを開始
- `/timer`: タイマーを開始
- `/vc_control`: VC 操作パネルを作成
- `/group_match`: 2人組 / 3人組を自動作成

### 追加 RAG ファイル

Bot 固有の説明、サーバー運用メモ、FAQ を別ファイルで持たせたい場合は次を使えます。

- `data/chat_rag.md`
- `data/chat_rag.json`
- `data/chat_rag.toml`

まずは `data/chat_rag.md` を使うのが一番簡単です。README と同様に会話中のローカル知識として参照されます。

### semantic memory の再インデックス

過去の JSON 履歴をあとから embedding DB に取り込みたい場合は次を実行します。

```bash
python bin/reindex_message_embeddings.py
```

これは `data/message_logs/*.json` を読み、`ollama.model_embedding` で batch embedding を作って `data/message_logs/message_vectors.sqlite3` に保存します。

### 議事録機能

- `/minutes_start` で録音開始、`/minutes_stop` または VC 無人で停止
- 発話単位でリアルタイム文字起こしを投稿
- 文字起こしは Google Speech-to-Text を優先
- Google 側の認証や API 失敗時のみ `faster-whisper` にフォールバック

### ゲーム機能

**人狼（最小構成）**
- `/game mode:人狼役職配布` で開始
- 参加者は `🎮` リアクションで参加
- 役職は DM で配布
- 人狼だけが DM に届くプレイヤー一覧メッセージへリアクションして襲撃対象を選ぶ
- 全人狼の投票が揃うと 1 人が死亡し、元チャンネルに通知

**あいうえおバトル**
- `/game mode:あいうえおバトル` で開始
- 1 人から開始可能
- お題は各参加者が DM で送信
- 条件は「ひらがなのみ」「7文字以下」「小文字/濁点/半濁点/ー可」

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
- 会話呼び出しの過度な利用（2 回 / 20 秒）

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
ollama pull gpt-oss:120b

# config.py で正しいモデル名を設定
OLLAMA_MODEL_DEFAULT = "gpt-oss:120b"
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
