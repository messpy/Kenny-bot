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

### ローカル検証

- `bin/debug_route.py` でルート判定や応答生成の preview ができます
- `--mock-llm` は AI / 検索の backend をモック化します
- `--dry-run-send` は送信だけ抑止して、それ以外の処理はできるだけそのまま流します
- `--trace-llm` を併用すると `trace/debug_route_trace.txt` にトレースを書き出します

## 設計

- 応答経路とモデル責務: [docs/response_architecture.md](/home/kennypi/work/Kenny-bot/docs/response_architecture.md)

## 注意

- AI 応答は間隔制限があります。連続送信しすぎないようご注意ください。
- スパムや迷惑行為は自動で検出・対応されます。
