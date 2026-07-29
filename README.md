# Kenny Bot

Discord で使える AI アシスタント Bot です。

## 基本的な使い方

Bot をメンションするか、Bot のメッセージにリプライすると、AI が応答します。
DM でも同様に応答できます。

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
| `/ping` | Bot の応答速度を確認 |
| `/bot_info` | Bot の状態を確認 |
| `/summarize_recent` | このチャンネルの直近メッセージを要約 |
| `/config` | 設定の表示・更新 |
| `/model_list` | 利用可能なモデル一覧を表示 |
| `/model_change` | Bot が使うモデルを切り替え |
| `/minutes` | 議事録の開始・停止・状態表示 |
| `/tts` | 読み上げの開始・停止・話者変更 |
| `/modpanel` | スパム管理パネルを作成 |
| `/birthday` | action で add/list/remove を切り替え、通知時刻も設定 |
| `/game` | 人狼・あいうえおバトル等のミニゲームを開始 |
| `/timer` | タイマーを開始 |
| `/vrchat_world` | VRChat のワールドを検索 |
| `/vrc_user` | VRChat ユーザーURLからプロフィールを取得 |

### ゲーム

- **人狼**: `/game mode:人狼` で開始。リアクションで参加表明、夜は DM で行動選択
- **Avalon**: `/game mode:Avalon` で開始。固定人数のクエスト、承認投票、暗殺まで進行
- **あいうえおバトル**: `/game mode:あいうえお` で開始。1 人から OK

### 議事録

VC 参加中に `/minutes` で録音開始。`action` で開始・停止・状態表示を切り替えます。

### 誕生日

`/birthday action:add` で登録するときに `notify_time:HH:MM` を指定できます。未指定なら 12:00 です。

### リアクション設定

固定リアクションは `config/bot_settings.yaml` の `reactions` で設定します。

```yaml
reactions:
  ai_review: 🤔
  weekly_today_language:
    unknown: ✋
    known: 👀
    learned: ✅
    issue: ⚠️
  mod_reset: 🔄
  mod_list: 📋
  vc:
    join: ✅
    mute_on: 🔇
    mute_off: 🎤
    deaf_on: 🙉
    deaf_off: 🙊
  group_match:
    join: 🤝
    start: ▶️
  minutes:
    summary: ⏯️
    stop: ⏹️
    playback: 🎶
    realtime: ▶️
  timer:
    restart: 🔁
  game:
    join: 🎮
    start: ▶️
  wordwolf:
    end: ⏹️
    repeat: 🔁
  werewolf:
    votes:
      - 1️⃣
      - 2️⃣
      - 3️⃣
      - 4️⃣
      - 5️⃣
      - 6️⃣
      - 7️⃣
      - 8️⃣
      - 9️⃣
      - 🔟
```

キーワード自動リアクションは既存互換のため、従来どおり `keyword_reactions` を維持します。
既存設定を壊さないよう、Bot は `keyword_reactions` を読み、必要な場合だけ `reactions.keyword` の値で上書きします。

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
