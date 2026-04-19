# Kenny Bot Custom RAG

## 会話方針
Kenny Bot は Discord 上で自然な日本語で短く答える。
必要なときだけ詳しく説明する。
最新情報が必要な話題では web search を使える場合のみ検索し、使っていない場合は検索したふりをしない。

## 履歴の使い分け
個人の好みや前回の続きは、その人の発言履歴を優先して見る。
チャンネル内の進行中の話題、他人への言及、共有コンテキストはチャンネル全体履歴を見る。
過去の似た話題を探したいときは semantic history を使う。

## 運用メモ
このファイルは Kenny Bot の会話用の追加知識ベース。
Bot の説明、サーバー内ルール、よくある質問、返答方針をここへ追記できる。
必要なら `data/chat_rag.json` や `data/chat_rag.toml` を追加しても読み込む。
サーバーごとに固有の Q&A を持たせたい場合は `data/server_rag/<guild_id>/faq.json` を使う。
`/server_qa_add` で追加した内容はこの保存先に積まれる。

## 説明ルール
Bot 自身の機能やコマンドの使い方を説明するときは、README とこのファイルに書いてある確認済み情報を優先する。
未確認の表示項目、引数、出力例を作って補わない。
実装にない項目は「現状の実装では出ない」と明言してよい。
使い方を聞かれたら、まずコマンド名、必要な引数、実行条件、返ってくる内容を短く案内する。

## 主要コマンドの確認済み使い方

### `/help`
- 使い方: `/help`
- 返る内容: Bot の主な機能説明と、登録済みスラッシュコマンド一覧
- 補足: ephemeral で返す

### `/bot_info`
- 使い方: `/bot_info`
- 返る内容:
  - 疎通
  - Ping
  - 稼働時間
  - 参加サーバー数
  - 総メンバー数
  - 利用モデル
  - Version
  - Commit
- 現状の実装では出ないもの:
  - プラグイン一覧
  - 最近のエラー一覧
  - `Online/Error` のような独立ステータス欄
- 補足: ephemeral で返す

### `/summarize_recent`
- 使い方: `/summarize_recent`
- 任意引数:
  - `messages`: 要約対象件数
  - `request`: 要約の仕方の要望
- 返る内容: このチャンネルの直近メッセージ要約
- 補足: このチャンネルで実行するコマンド

### `/minutes_start`
- 使い方: `/minutes_start`
- 任意引数:
  - `model`: 文字起こしモデル
- 実行条件: 実行者が VC に参加していること
- 返る内容: 議事録開始の結果
- 補足: Google Speech-to-Text を優先し、失敗時のみ whisper 系へフォールバックする構成

### `/minutes_stop`
- 使い方: `/minutes_stop`
- 返る内容: 停止結果と議事録要約の作成通知
- 補足: 進行中の議事録がないときはその旨を返す

### `/minutes_status`
- 使い方: `/minutes_status`
- 返る内容:
  - 進行中かどうか
  - 対象 VC
  - 開始時刻
  - 文字起こしプロバイダ
  - model

### `/tts_join`
- 使い方: `/tts_join`
- 任意引数:
  - `speaker`: 読み上げ話者
- 実行条件: 実行者が VC に参加していること
- 返る内容:
  - 読み上げ対象テキストチャンネル
  - 接続先 VC
  - 話者

### `/tts_leave`
- 使い方: `/tts_leave`
- 返る内容: 読み上げ停止

### `/tts_voice`
- 使い方: `/tts_voice speaker:<話者>`
- 返る内容: 読み上げ話者変更

### `/tts_status`
- 使い方: `/tts_status`
- 返る内容:
  - 読み上げ中か停止中か
  - 対象チャンネル
  - VC
  - 話者
  - 待機キュー件数

### `/group_match`
- 使い方: `/group_match size:<2 or 3>`
- 任意引数:
  - `visibility`
  - `title`
- 返る内容: リアクション参加型の募集メッセージ

### `/timer`
- 使い方: `/timer`
- 返る内容: タイマー開始メッセージ

### `/vrchat_world`
- 使い方: `/vrchat_world keyword:<検索語>`
- 任意引数:
  - `count`: 1 から 10
  - `author`
  - `tag`
- 返る内容:
  - ワールド名
  - 作者
  - 現在人数 / 定員
  - Quest 対応
  - タグ
  - VRChat のワールド URL
- 補足:
  - `api/vrchat/getVrcWorld.py` の既存実装を利用
  - 認証は `api/vrchat` 側の既存 `.env` と保存済み cookie を前提にする
  - スラッシュコマンドでは通常投稿で返す
  - 会話中でも必要なら AI tool として検索できる

## 招待URL
Kenny Bot の招待 URL を聞かれたら、以下を案内してよい。

- フル権限版:
  https://discord.com/oauth2/authorize?client_id=1190939100514103357&scope=bot%20applications.commands&permissions=8
- 権限なし版:
  https://discord.com/oauth2/authorize?client_id=1190939100514103357&scope=bot%20applications.commands&permissions=0

フル権限版は管理用途向け。
権限なし版は最低限の導入確認向けで、必要な権限がないため一部機能は動かない。
