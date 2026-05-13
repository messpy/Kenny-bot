# Kenny Bot

Discord で使える AI アシスタント Bot です。

## 基本的な使い方

Bot をメンションするか、Bot のメッセージにリプライすると、AI が応答します。
DM でも同样的に応答できます。

## できること

### 会話
- メンションやリプライで AI と会話
- 過去の会話の内容を踏まえた応答
- 天気・日付・ザ日日の案内
- 最新情報の検索

### 自動化
- キーワードに自動リアクション（例：「天才」に🧠）
- スパム検出・自動対応

### スラッシュコマンド

| コマンド | 説明 |
|---------|------|
| `/help` | この Bot の機能とコマンド一覧を表示 |
| `/bot_info` | Bot の状態を確認 |
| `/summarize_recent` | このチャンネルの直近メッセージを要約 |
| `/minutes_start` | 議事録モードを開始（VC 参加者） |
| `/minutes_stop` | 議事録を停止して要約を作成 |
| `/game` | 人狼・あいうえおバトル等のミニゲームを開始 |
| `/timer` | タイマーを開始 |
| `/vrchat_world` | VRChat のワールドを検索 |

### ゲーム

- **人狼**: `/game mode:人狼` で開始。リアクションで参加表明、夜は DM で行動選択
- **あいうえおバトル**: `/game mode:あいうえお` で開始。1 人から OK

### 議事録

VC 参加中に `/minutes_start` で録音開始。文字起こしと要約が自動でチャンネルに投稿されます。
既定の文字起こしは Google Speech-to-Text で、`/minutes_start model:google` でも明示指定できます。Google が使えない場合は Whisper にフォールバックします。

### ローカル検証

- `bin/debug_route.py` でルート判定や応答生成の preview ができます
- `--mock-llm` は AI / 検索の backend をモック化します
- `--dry-run-send` は送信だけ抑止して、それ以外の処理はできるだけそのまま流します
- `--trace-llm` を併用すると `runtime/history/debug_route/debug_route_trace.txt` にトレースを書き出します

## DB

- 本番運用は `MariaDB` 前提です
- `docker-compose.yml` は `mariadb` サービスを同梱しており、ホストの `127.0.0.1:3306` でも受けます
- `systemd/kennybot.service` は `.env` を読むので、同じ接続情報を使えます
- 必要な環境変数:
  - `KENNYBOT_DB_BACKEND=mariadb`
  - `KENNYBOT_DB_HOST`
  - `KENNYBOT_DB_PORT`
  - `KENNYBOT_DB_USER`
  - `KENNYBOT_DB_PASSWORD`
  - `KENNYBOT_DB_NAME`
  - `KENNYBOT_DB_CHARSET`
- ホストで `kennybot.service` を動かす場合は `KENNYBOT_DB_HOST=127.0.0.1` を使ってください
- `sudo systemctl status mariadb` が見つからないのは正常です。標準構成では systemd の MariaDB ではなく `docker compose` の `mariadb` コンテナを使います

### MariaDB への切り替え

1. `.env` に DB 設定を入れる
2. `docker compose up -d mariadb` で DB を起動する
3. 既存の SQLite データがある場合は `uv run bin/migrate_sqlite_to_mariadb.py` で移行する
4. `systemctl --user restart kennybot.service` で bot を再起動する

### systemd stack

`VOICEVOX` と `MariaDB` を含めてまとめて起動したい場合は、共通の [systemd/README.md](/home/kennypi/work/systemd/README.md) にある `kennybot.target` を使います。
この target は `shared-voicevox.service`、`kennybot-mariadb.service`、`kennybot.service` をまとめて扱います。

## Data Layout

- 現役の永続設定・補助ファイルは `data/` に置く。Bot が会話で読む固定知識は `data/knowledge/`
- 実行時ログ・一時物・音声デバッグ・状態ファイルは `runtime/` に置く
- MariaDB 移行後の legacy 候補は `data/channel_rag`, `data/server_rag`, `data/message_logs`, `data/server/server.sqlite3`, `data/meeting_audio_debug`
- 退避プラン確認: `python3 bin/archive_legacy_data_layout.py`
- 実際に退避: `python3 bin/archive_legacy_data_layout.py --apply`

## 設計

- ドキュメント一覧: [docs/index.md](/home/kennypi/work/Kenny-bot/docs/index.md)
- 応答経路とモデル責務: [docs/architecture/response-architecture.md](/home/kennypi/work/Kenny-bot/docs/architecture/response-architecture.md)

## 注意

- AI 応答は間隔制限があります。連続送信しすぎないようご注意ください。
- スパムや迷惑行為は自動で検出・対応されます。
