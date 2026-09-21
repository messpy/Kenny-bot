# Kenny Bot 設計書

## 1. 目的

この文書は、Kenny Bot の現行実装を前提にした実装者向け設計書である。  
README は利用者向けの概要、`docs/architecture/response-architecture.md` は応答品質に関する規範、`docs/architecture/message-flow.md` は詳細フローに特化し、本書はそれらをつなぐ全体設計を扱う。

## 2. システム概要

Kenny Bot は Discord 上で動作する多機能 Bot で、主に次の責務を持つ。

- メンション、リプライ、DM を起点にした AI 会話
- チャンネルやサーバーに閉じた履歴、RAG、プロフィール参照
- スラッシュコマンドによる補助機能
- スパム抑止と簡易モデレーション
- 議事録、音声、ゲーム、リアクションなどの周辺機能

現行の実装本体は `src/kennybot/` 配下に集約しており、起動や保守スクリプトは `bin/` から本体を呼び出す。

## 3. 設計方針

- Discord イベント処理を止めないことを優先する
- AI 応答は単発生成ではなく、履歴、ローカル知識、プロフィール、必要時の外部情報を合成する
- ギルド境界を壊さないことを最重要制約とする
- 最新性が必要な問い合わせだけ外部検索へ逃がす
- 実装の中心は `src/kennybot/` に寄せ、リポジトリ直下には本体ロジックを増やさない
- 永続DBと静的Bot知識は `data/`、ログ、キャッシュ、状態、一時ファイルは `runtime/` に分離して置く

## 4. ディレクトリ責務

- `bin/`
  起動、再索引、プレビュー、補助メンテナンス用スクリプト。
- `src/kennybot/`
  現行の実装本体。
- `src/kennybot/cogs/`
  Discord イベントと slash command の受け口。
- `src/kennybot/ai/`
  Ollama 呼び出し、検索、要約など AI 周辺処理。
- `src/kennybot/features/`
  機能単位で分離を進める層。現状は chat が先行している。
- `src/kennybot/utils/`
  設定、ログ、RAG、履歴、ベクトル保存、ツール実行などの基盤処理。
- `src/kennybot/guards/`
  スパム判定とモデレーション実行。
- `data/`
  永続データ。Bot 固定知識、サーバー情報、ゲーム用静的データなど。
- `runtime/`
  実行中生成物。キャッシュ、履歴、状態、一時ファイル。
- `docs/`
  実装者向け設計文書と開発用補足文書の配置先。`architecture/` と `development/` に分ける。

## 5. 起動アーキテクチャ

起動入口は `bin/run.py` である。ここで排他制御、環境変数読み込み、Bot 生成を行い、`src/kennybot.bot.MyBot` を起動する。

### 5.1 起動シーケンス

1. `setup_logging()` でログ設定を初期化
2. `acquire_lock(runtime/state/kennybot.lock)` で多重起動を防止
3. `.env` を読み込み、`DISCORD_TOKEN` を必須検証
4. `src/kennybot.bootstrap.create_bot()` で `MyBot` を生成
5. `bot.run(token)` で Discord Gateway に接続

### 5.2 `MyBot` 初期化責務

`src/kennybot/bot.py` の `MyBot` がアプリケーションの構成ルートである。

- `SpamGuard` を設定値から構築
- `MeetingMinutesManager` を初期化
- `AIProgressTracker` を初期化
- Ollama 実行系として `OllamaRunner` を準備
- 会話系として `ChatMemory` と `ChatService` を準備
- 検索系として `AISearchService` を準備
- `OLLAMA_HOST` / `OLLAMA_EMBED_HOST` を見て Ollama Client API を構築
- `setup_hook()` で Cog 群を登録
- `on_ready()` で slash command を同期
- `on_app_command_error()` と `on_error()` でグローバル例外を集約

## 6. 実行時コンポーネント

### 6.1 Cog 層

- `MessageLogger`
  通常メッセージ処理の中心。AI 応答、履歴収集、RAG、Web 検索判断、イベント記録を担う。
- `SlashCommands`
  `/help`、`/bot_info`、要約、議事録、VRChat 検索などの slash command を提供する。
- `VoiceLogger`
  議事録や VC 関連のイベント処理を担う。
- `TTSReader`
  読み上げ系機能を提供する。
- `GameCommands`
  ミニゲーム系のコマンド入口。
- `MemberLogger` / `AuditLogger`
  メンバー、監査系のイベント記録。
- `ModPanel` / `ReactionRoles`
  モデレーション UI やロール付与機能。

### 6.2 サービス層

- `ChatService`
  旧来の会話実行サービス。短い履歴付き生成を担当する。
- `OllamaClientService` 相当の Client API 経路
  現在の `MessageLogger` 側のツール呼び出し付き応答で使う。
- `AISearchService`
  DuckDuckGo 検索と Web 要約をまとめる。
- `LiveInfoService`
  最新性が必要な文脈の補助情報を扱う。
- `LocalRAG`
  `data/knowledge/` やギルド別データからローカル知識を返す。
- `MessageVectorStore`
  メッセージ埋め込みの保存と意味検索を担う。
- `MessageFetcher`
  Discord 履歴から recent turns や user history を収集する。

### 6.3 Guard 層

- `SpamGuard`
  通常メッセージ頻度、AI 呼び出し頻度、重複発話を検査する。
- `ModActions`
  警告、削除、Timeout など実際の対応を実行する。

## 7. メッセージ処理設計

通常会話の中心は `src/kennybot/cogs/message_logger.py` の `on_message` 系処理である。  
この層は単に LLM を呼ぶのではなく、「応答すべきか」「何を参照すべきか」「どこまで外部情報に頼るか」を先に決める。

現行実装では、この会話処理の複数ステージが `MessageLogger` 内に混在している。  
すなわち、受信判定、初期コンテキスト収集、Planner 呼び出し、取得実行、整形、Final LLM 入力組み立て、送信、ログ保存が一つの Cog に集約されている。  
これは現時点の運用と互換性を優先した構成であり、今回の設計更新はこの実装を壊さずに、将来の分離目標を明文化するものである。

### 7.1 一次判定

最初に次を除外または分岐する。

- Bot 自身の発言
- Webhook 発言
- DM
- `kenny-chat` 中継対象
- スパム違反

### 7.2 応答対象判定

AI 応答へ進む条件は主に次である。

- Bot へのメンション
- Bot 発言へのリプライ
- 直前メンション後の短時間継続会話

それ以外の通常メッセージでは、埋め込み登録とキーワードリアクションが主処理になる。

### 7.3 AI 応答フロー

AI 応答時は次の順で処理する。

1. 特殊クエリ判定
   議事録開始停止、Bot 機能説明、モデル情報などを先に捌く。
2. AI レート制限判定
   `SpamGuard.allow_ai()` 相当で連続呼び出しを抑止する。
3. メッセージ索引予約
   応答の有無に関係なく埋め込み登録を進める。
4. 取得プラン決定
   `retrieval_plan_prompt` で使う情報源を決め、失敗時はルールベースにフォールバックする。
5. コンテキスト収集
   recent turns、user history、reply chain、profile、RAG、コマンド一覧、必要時の Web 検索を集める。
6. Final LLM 実行
   Planner が選んだ情報源の実行結果だけを入力にして応答を生成する。
7. 後処理
   整形、無害化、長文分割、ログ送信を行う。

詳細フローは `docs/architecture/message-flow.md` を参照する。

### 7.4 正式な目標フロー

正式な目標フローは、現在の `MessageLogger` 集約型実装を次の 8 段階へ整理したものである。

1. メンション / DM / 返信の受信
2. Minimal Context Builder
3. Planner LLM
4. Tool Resolver
5. Context Compressor
6. Final Prompt Builder
7. Final LLM
8. Discord 送信 + Log Writer

### 7.5 8段階フローの定義

#### 1. メンション / DM / 返信の受信

Discord イベントから会話対象を受け取り、Bot 自身の投稿、Webhook、対象外入力、スパム違反などの一次判定を行う。

#### 2. Minimal Context Builder

Planner へ渡す前の最低限コンテキストを組み立てる段階である。  
少なくとも次をここで揃える。

- ユーザー ID
- サーバー ID
- チャンネル ID
- 発言本文
- 返信元
- 添付情報
- 直近ユーザー発言

現行実装ではこの処理は独立コンポーネントではなく、`MessageLogger` 内の履歴取得や文脈解決処理に分散している。

#### 3. Planner LLM

Minimal Context を受け、「この質問に答えるには何を読むべきか」を判断する段階である。  
Planner は `available_tools` カタログを見て、必要な情報源と取得方針を JSON Plan として返す。

#### 4. Tool Resolver

Planner の JSON Plan を受け、必要な情報源を Python 側で実行する段階である。  
Discord 情報、履歴、RAG、Web/API、Bot 状態、ログ情報などの実取得はここに属する。

#### 5. Context Compressor

取得結果をそのまま Final LLM に渡さず、長すぎる履歴や RAG、検索結果を整理、圧縮、重複除去する段階である。  
最終的には、回答に必要な情報だけを短く保った構造化コンテキストへ落とし込む。

場所説明系では特に、ユーザー向け説明と運用メモを分離する。  
たとえば `ワールド概要` は回答候補に残し、`運用方針` や `オーナー向けメモ` のような内部寄りセクションは Final LLM に渡す前に除外または優先度を下げる。

#### 6. Final Prompt Builder

元の質問、Planner の判断、Tool 実行結果、圧縮済みコンテキストを Final LLM 向けの入力へ変換する段階である。  
Prompt template、system message、現在時刻、場所情報、制約条件の注入もここに属する。

#### 7. Final LLM

Final Prompt Builder が組み立てた入力を使い、ユーザー向け自然文を生成する段階である。  
内部 JSON、tool 名、planner の内部表現はここでユーザー表示文へ露出させない。

#### 8. Discord 送信 + Log Writer

生成した自然文を Discord に返し、あわせてユーザー発言、使用情報源、AI 応答、エラーをログへ保存する段階である。  
送信処理と監査ログ記録を最終段に集約する。

ユーザーがメンション経由で明示的に修正を求めた場合は、ログ保存だけで終わらせず、その指摘内容を user-provided RAG として保存する。
基本は channel スコープに追記し、サーバー説明やワールド説明のような場所説明系の修正は guild スコープにも mirror する。

### 7.6 現行実装との差分

現行実装は概念的には上記 8 段階を担っているが、責務境界はまだ明確に分離されていない。

- 受信、Minimal Context Builder、Planner 呼び出し、Tool 実行、Final Prompt Builder、送信、ログ保存の多くが `MessageLogger` に同居している
- Context Compressor は独立段ではなく、取得結果の整形処理として文脈構築中に混在している
- 場所説明用 RAG にユーザー向け説明と運用メモが同居する場合があり、現行実装ではこの分離が不十分な箇所がある
- Final LLM には圧縮済みコンテキストは渡っているが、Planner の JSON Plan 自体は明示的入力としてはまだ渡していない
- 初期コンテキストの最低単位は可変設定中心であり、「直近ユーザー履歴 3 件」がまだ固定契約になっていない

したがって、現在の実装は「8段階フローを一つのクラスで連続実行している状態」とみなせる。

### 7.7 Codex モードの正式定義

`Codexモード` は単なる修正ログ記録ではなく、Bot 自身のソースコード修繕ジョブを起票するための実行モードと定義する。
正式な意味は「ユーザーの修正要求を受け、Kenny-bot のコードベースに対する専用ブランチと作業 worktree を作り、Codex に修繕作業を開始させること」である。

最低限の責務は次の通り。

1. 修正要求の検知
   Bot への不満、不具合、反映漏れ、誤説明を通常会話と区別して拾う。
2. 修正対象の文脈確定
   直前の質問、直前の Bot 応答、対象チャンネル、対象 guild、推定修正箇所を束ねる。
3. Codex 修繕ジョブ起票
   一意な job ID を発行し、修正依頼の状態ファイルと履歴ログを `runtime/state/` と `runtime/history/` に保存する。
4. 専用ブランチ / worktree 作成
   既存作業ツリーを壊さず、現在の `HEAD` から `codex/...` 系ブランチを切り、独立 worktree を作る。
5. Codex 実行開始
   job 用 prompt を渡して `codex exec` を非対話で起動し、そのブランチ内で調査、修正、テストを進めさせる。
6. 実行状態の監査
   起動可否、PID、終了コード、結果要約、差分の有無を job 状態へ反映し、管理ログにも残す。
7. Discord への返答
   ユーザーには「修正依頼を受け付けた」だけでなく、「修正ブランチを作成して作業を開始した」事実を自然文で返す。

`Codexモード` の名称を使う条件は、少なくとも 3 から 5 を実際に満たしている場合に限る。
修正内容を記録するだけでブランチも Codex 実行も発生しない場合は、それは `修正依頼モード` であり `Codexモード` ではない。

## 8. Retrieval 設計

Kenny Bot の応答品質は、プロンプトよりも「何を取得して渡したか」に強く依存する。  
そのため `MessageLogger` では Retrieval Planner を前段に置いている。  
ここでいう `available_tools` は固定の関数一覧ではなく、Planner LLM に渡す「取得可能な情報源カタログ」を指す。

### 8.1 Planner と Final LLM の責務分離

- Planner LLM
  ユーザー発話と現在の Discord 文脈を見て、どの情報源を取得するべきかを選ぶ。
- 実行層
  Planner の選択に従って Python 側が情報源を実行し、必要なデータだけを収集する。
- Final LLM
  実行済みの取得結果だけを入力にして最終回答を書く。

この分離により、最終回答の品質は「どの tool 名を選んだか」ではなく、「どの情報が取得され、どの形で Final LLM に渡ったか」で管理する。

### 8.2 `available_tools` カタログ

Planner LLM には、固定リストの低レベル API ではなく、取得可能な情報源カタログとして次の名前を渡す。

- Discord 系
  `get_server_info`
  `get_channel_info`
  `get_user_info`
  `get_member_profile`
  `get_recent_messages`
  `get_user_recent_messages`
  `get_channel_recent_messages`
  `get_reply_message`
  `get_reply_chain`
  `get_message_by_id`
  `get_thread_messages`
  `get_mentions_in_message`
  `get_attachment_metadata`
  `analyze_attachment`
- 履歴 / RAG 系
  `search_semantic_history`
  `get_local_knowledge`
  `get_guild_rag`
  `get_channel_rag`
  `get_member_history`
  `get_channel_profile`
  `get_bot_memory`
  `get_conversation_summary`
- Bot 機能情報系
  `get_bot_command_catalog`
  `get_bot_game_catalog`
  `get_runtime_model`
  `get_bot_status`
  `get_available_tools`
- 外部 API 系
  `web_search`
  `web_fetch`
  `get_weather`
  `search_vrchat_world`
  `get_url_metadata`
- ログ / 管理系
  `log_user_message`
  `log_ai_output`
  `send_event_log`
  `get_recent_bot_logs`
  `get_error_logs`
- 安全 / 制御系
  `check_spam_guard`
  `check_ai_rate_limit`
  `enqueue_ai_request`
  `get_queue_status`

Planner はこのカタログから必要な情報源を選び、実行層はそれを実データ取得へ解決する。  
`get_available_tools` はユーザー向けの内部一覧ではなく、Planner が利用可能な取得範囲を再確認するためのメタ情報源として扱う。

### 8.3 主な情報源の解決イメージ

- `get_user_recent_messages` / `get_member_history`
  発話者や対象人物の直近履歴を返す。
- `get_recent_messages` / `get_channel_recent_messages`
  チャンネルの直近会話を返す。
- `get_reply_message` / `get_reply_chain`
  返信元の文脈を返す。
- `search_semantic_history`
  類似埋め込み検索を返す。
- `get_channel_profile` / `get_channel_info` / `get_server_info`
  サーバーやチャンネルの説明を返す。
- `get_member_profile` / `get_user_info`
  ユーザーや参加者の説明を返す。
- `get_local_knowledge` / `get_guild_rag` / `get_channel_rag`
  `data/knowledge/` やギルド別 RAG を返す。
- `get_bot_command_catalog` / `get_bot_game_catalog`
  Bot 機能や slash command の説明を返す。
- `get_runtime_model` / `get_bot_status` / `get_queue_status`
  実行中モデルや稼働状態を返す。
- `search_vrchat_world`
  VRChat ワールド検索結果を返す。
- `web_search` / `web_fetch` / `get_weather` / `get_url_metadata`
  最新性や外部参照が必要な情報を返す。

上記は Planner に見せる情報源名と、その代表的な解決イメージである。  
内部実装では統合、別名、将来の差し替えを許容するが、Planner 契約としての意味は保つ。

### 8.4 取得ポリシー

- 人物質問では `member_profile` と `member_history` を優先する
- サーバー、チャンネル説明では `channel_profile` を最優先する
- Bot の使い方質問では `bot_command_catalog` と `local_knowledge` を優先する
- 最新ニュース、天気、価格、在庫などは `web_search` を追加する
- 取得計画の AI 出力が壊れた場合でも `_fallback_retrieval_plan()` で最低限の応答を維持する

### 8.5 Final LLM への入力制約

- Final LLM には tool 実行結果のみを渡す
- tool 名、内部 JSON、planner の生出力は原則渡さない
- ユーザー向け回答には tool 名や内部トレースを出さない
- 必要なら出典 URL や参照先だけを自然文に埋め込む

この制約は、最終回答を自然な日本語に保ちつつ、内部実装の変更が回答文面に漏れないようにするためのもの。

### 8.6 規範文書との関係

`docs/architecture/response-architecture.md` は特に `serverinfo` 系質問の優先順位を固定する規範文書である。
実装は次の制約を守る必要がある。

- ギルド外のデータを place description に混ぜない
- `serverinfo` で recent turns を主根拠にしない
- ローカルな場所説明に Web 検索を優先しない
- 最終回答に planner や tool の内部表現を漏らさない

## 9. AI バックエンド設計

### 9.1 主経路

通常会話の主経路は Ollama である。

- `OLLAMA_MODEL_DEFAULT`
  既定モデル。
- `OLLAMA_MODEL_CHAT`
  会話向け主モデル。
- `OLLAMA_MODEL_SUMMARY`
  要約やフォールバックで使う候補。

### 9.2 二つの実行経路

現状は AI 呼び出しが二系統ある。

- `OllamaRunner`
  subprocess/asyncio ベースの旧実装。
- `ollama_client.chat_simple(...)`
  Client API ベースの新しめの経路。

設計上は後者を主経路として整理していくが、現時点では互換のため両方が共存している。

### 9.3 外部情報補助

検索は `DuckDuckGoSearch` を使い、その要約や最終回答の統合に Ollama を使う。  
外部検索は「最新性」「価格」「在庫」「ニュース」など時点依存の質問に限定する。

## 10. データ配置

### 10.1 永続データ

- `data/server/`
  サーバー関連の補助データ。MariaDB 移行後は `server.sqlite3` は legacy 扱いで、主系 DB は MariaDB。
- `data/knowledge/`
  Bot が会話で読む固定知識。人間向け README や設計文書とは分ける。
- `data/wordwolf_pairs.json`
  ゲーム用の静的データ。

### 10.2 実行時データ

- `runtime/cache/`
  キャッシュ。
- `runtime/history/`
  実行履歴や補助出力。音声デバッグもここに置く。
- `runtime/logs/`
  実行時ログ。メッセージログの JSON 退避もここに集約する。
- `runtime/state/`
  実行中状態。多重起動防止ロックや message claim をここに置く。
- `runtime/tmp/`
  一時ファイル。
- `runtime/old/`
  legacy データの退避先。

### 10.2.1 Legacy データ

- `data/channel_rag/`
- `data/server_rag/`
- `data/message_logs/`
- `data/meeting_audio_debug/`

これらは互換性維持や移行残りとして残りうるが、主系の保存先ではない。

### 10.3 スコープ規則

ギルド境界は `scoped_data.py` の責務で管理する。  
メッセージイベントでは `msg.guild.id`、インタラクションでは `interaction.guild_id` を起点にし、別ギルドのデータへ暗黙フォールバックしてはいけない。

## 11. ログと障害対応

### 11.1 ログの種類

- 通常ログ
  `setup_logging()` で初期化するプロセスログ。
- イベントログ
  `send_event_log()` による Discord 管理ログ送信。
- 会話ログ
  `log_user_message()`、`log_ai_output()` などの会話監査ログ。
- 修正モードログ
  `log_codex_repair_mode()` による障害や不満フィードバックの記録。

### 11.2 障害時の基本方針

- 起動失敗、slash sync 失敗、未処理例外は管理ログへ送る
- 同種のイベント例外は短時間で抑制し、ログスパムを防ぐ
- ユーザーには実装内部ではなく、失敗した事実だけを簡潔に返す

### 11.3 Codex ジョブ監査

- Codex ジョブ状態は `runtime/state/codex_jobs/` に JSON として保存する
- Codex 実行の標準出力、最終応答、stderr は `runtime/history/codex_jobs/` に保存する
- 監査対象には job ID、branch 名、worktree パス、起動時刻、終了時刻、終了コード、差分有無を含める
- worktree 作成失敗、`codex` 実行失敗、結果ファイル欠落は通常会話エラーと分けて管理ログへ送る
- `Codexモード=あり` の管理ログは、少なくとも job ID と branch 名を追跡できる状態を目標とする

## 12. 既知の設計上の状態

- `src/kennybot/` への移行は進行中で、互換ラッパーが残っている
- AI 呼び出し経路が `Runner` と `Client API` の二系統で共存している
- `runtime/` と `data/` の責務分離は、実行時状態を `runtime/state/`、Bot 固定知識を `data/knowledge/` に寄せる方針で進めている

これらは段階移行を止めずに運用を続けるための暫定構成である。

## 13. 今後の整理方針

- 会話処理を `features/chat/` 中心へさらに寄せる
- AI 呼び出し経路を一つの主実装へ整理する
- place description 系の retrieval 契約をテストで固定する
- `data/` と `runtime/` の役割をより厳密に分離する
- 設計文書の責務を `README` と `docs/` で明確に保つ

### 13.1 会話処理リファクタ方針

- 直近ユーザー履歴 3 件を初期コンテキストの最低単位にする
- 追加履歴は Planner の判断で取得する
- Planner の JSON Plan を Final LLM に明示的に渡す
- 取得結果の圧縮処理を独立関数または独立クラスへ切り出す
- ツール実行結果は `references` / `reference_details` / `web_queries` と紐づけてログに残す
- メンション修正要求は user-provided RAG として channel/guild FAQ に残し、後続応答で再利用できるようにする

### 13.2 Codex モード実装修繕方針

- `Codexモード` は修正ログだけでなく、branch / worktree / job state を伴う実行モードへ寄せる
- job 起票と実行起動は `MessageLogger` の責務から切り出し、専用 utility か service に寄せる
- Bot 本体の作業ツリーは直接編集せず、常に worktree 上で Codex を動かす
- Codex 実行結果は Discord 向け自然文、管理ログ、job 状態ファイルの三系統で追跡できるようにする
- job の完了通知、テスト結果要約、差分確認は次段階で強化するが、まずは起票と作業開始を保証する

## 14. 参照文書

- `README.md`
- `docs/architecture/message-flow.md`
- `docs/architecture/response-architecture.md`
- `docs/development/local-profile-preview.md`
- `docs/development/local-tool-api.md`
