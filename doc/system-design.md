# Kenny Bot 設計書

## 1. 概要

Kenny Bot は Discord サーバー向けの多機能 Bot であり、主に以下を提供する。

- AI 会話応答
- スパム検知とモデレーション補助
- 音声読み上げ、文字起こし、要約
- ゲーム系コマンド
- メッセージ履歴と意味検索ベースの補助記憶

README は利用者向けの概要とセットアップに絞り、本書は実装者向けの設計資料として扱う。

## 2. 設計目標

- Discord 上のイベントを安定して処理する
- AI 機能を Bot 本体から分離し、差し替えや拡張をしやすくする
- メッセージ履歴、ローカル知識、外部情報を組み合わせて応答品質を上げる
- スパムや過剰な AI 呼び出しを抑制し、運用コストを下げる
- 既存コードを維持しながら、将来的に `src/` 中心の構成へ整理できるようにする

## 3. 論理構成

### 3.1 モジュール責務

- `bin/`
  - 起動エントリポイント
  - `.env` 読み込み
  - 多重起動防止
- `bot.py`
  - Bot インスタンス生成
  - 主要サービス初期化
  - Cog 登録
  - グローバルエラーハンドリング
- `cogs/`
  - Discord イベントや slash command の受け口
  - 個別機能単位のユースケース制御
- `ai/`
  - Ollama 実行
  - 会話生成
  - 外部検索や要約
  - 音声認識や画像補助
- `guards/`
  - スパム判定
  - モデレーション実行
- `utils/`
  - 設定、ログ、履歴、ベクトル保存、RAG、補助処理
- `doc/`
  - 設計書、仕様書、運用文書
- `src/`
  - 将来の実装コードの集約先候補

### 3.2 全体アーキテクチャ

```mermaid
flowchart TD
    U[Discord User] --> D[Discord Gateway / Interaction]
    D --> B[MyBot]
    B --> C1[MessageLogger]
    B --> C2[SlashCommands]
    B --> C3[TTSReader]
    B --> C4[GameCommands]
    B --> C5[VoiceLogger / MemberLogger / AuditLogger]

    C1 --> G[SpamGuard]
    C1 --> R1[LocalRAG]
    C1 --> R2[LiveInfoService]
    C1 --> R3[MessageStore]
    C1 --> R4[MessageVectorStore]
    C1 --> A1[OllamaClient / ChatService]

    G --> M[ModActions]
    A1 --> O[Ollama]
    R2 --> W[Web / External Sources]
    R3 --> DS[(JSON Logs)]
    R4 --> DB[(SQLite Vector DB)]
```

## 4. 起動設計

起動は `bin/run.py` を起点とし、環境読み込みと排他制御を先に行う。

### 4.1 起動シーケンス

```mermaid
sequenceDiagram
    participant CLI as bin/run.py
    participant Lock as SingleInstance
    participant Env as utils.env
    participant Bot as MyBot
    participant Discord as Discord API

    CLI->>Lock: acquire_lock(data/kennybot.lock)
    Lock-->>CLI: success / fail
    CLI->>Env: load_env_file()
    CLI->>Env: require_env(DISCORD_TOKEN)
    CLI->>Bot: instantiate MyBot(...)
    Bot->>Bot: initialize spam guard / AI / services
    CLI->>Discord: bot.run(token)
    Discord-->>Bot: on_ready / events
```

### 4.2 起動時の重要ポイント

- 多重起動は `data/kennybot.lock` で防止する
- `MyBot` 初期化時に AI クライアント、スパムポリシー、議事録管理、進捗トラッカーを組み立てる
- `setup_hook()` で Cog を登録する
- `on_ready()` で slash command 同期を実施する

## 5. 会話処理設計

`cogs/message_logger.py` が通常メッセージ処理の中心である。ここでリアクション、会話、履歴保存、意味検索、外部情報補助が交差する。

### 5.1 メッセージ処理フロー

```mermaid
flowchart TD
    A[Message Received] --> B{Bot/対象外?}
    B -- Yes --> Z[Ignore]
    B -- No --> C[Normalize Text]
    C --> D[SpamGuard Check]
    D --> E{Violation?}
    E -- Yes --> F[Warn / Delete / Timeout / Kick / Ban]
    E -- No --> G[Persist Message Log]
    G --> H[Schedule Embedding Index]
    H --> I{Mention / Reply / Trigger?}
    I -- No --> J[Optional Keyword Reaction]
    I -- Yes --> K[Build Context]
    K --> L[LocalRAG + History + Semantic Memory + Live Info]
    L --> M[Call Ollama]
    M --> N[Send Response]
```

### 5.2 応答コンテキストの構成

AI 応答時は単一の情報源ではなく、複数の補助コンテキストを組み合わせる。

- 直近の会話履歴
- 発言者や返信先に応じた対象ユーザー履歴
- `LocalRAG` によるローカル知識
- `MessageVectorStore` による意味的に近い過去発言
- 必要時のみ `LiveInfoService` による外部情報

### 5.3 会話フロー詳細

```mermaid
flowchart LR
    U[User Prompt] --> H1[Recent History]
    U --> H2[Target User Context]
    U --> H3[LocalRAG]
    U --> H4[Semantic Memory Search]
    U --> H5[Live Info Decision]
    H1 --> P[Prompt Assembly]
    H2 --> P
    H3 --> P
    H4 --> P
    H5 --> P
    P --> O[Ollama Chat]
    O --> R[Discord Reply]
```

## 6. メッセージ保存設計

メッセージ保存は二層で行う。

- 可読な履歴保存: JSON ベース
- 類似検索用保存: SQLite ベースのベクトルストア

### 6.1 保存の狙い

- 監査や会話文脈の再利用を可能にする
- 同一チャンネルや関連ユーザーの過去発言を参照できるようにする
- embedding を用いた意味検索により、単純な全文一致では拾えない関連会話を取得する

### 6.2 保存フロー

```mermaid
sequenceDiagram
    participant Msg as Discord Message
    participant Cog as MessageLogger
    participant Log as MessageStore
    participant Emb as Embed Client
    participant Vec as MessageVectorStore

    Msg->>Cog: on_message
    Cog->>Log: save raw message log
    Cog->>Emb: embed(content)
    Emb-->>Cog: vector
    Cog->>Vec: upsert_message(...)
```

## 7. モデレーション設計

モデレーションは `SpamGuard` と `ModActions` の分担で構成される。

- `SpamGuard`
  - 投稿頻度
  - 重複メッセージ
  - AI 呼び出し頻度
  - 警告クールダウン
- `ModActions`
  - メッセージ削除
  - タイムアウト
  - キック
  - バン

### 7.1 モデレーション判定フロー

```mermaid
flowchart TD
    A[Incoming Message] --> B[SpamGuard Evaluate]
    B --> C{Threshold Exceeded?}
    C -- No --> D[Continue Processing]
    C -- Yes --> E[Update Violation Level]
    E --> F[Select Action]
    F --> G[Execute ModActions]
    G --> H[Send Event Log / Panel Update]
```

## 8. 音声・周辺機能設計

音声系は会話系とは別責務で動くが、Bot 本体の初期化とイベント基盤を共有する。

- `TTSReader`
  - VOICEVOX 読み上げ
- `VoiceLogger`
  - ボイスチャンネル関連イベント
- `ai/google_speech.py`
  - Google Speech-to-Text
- `utils/meeting_minutes.py`
  - 議事録管理

音声認識は Google Speech-to-Text を優先し、失敗時に別系統へフォールバックする想定で設計されている。

## 9. エラー処理方針

- アプリコマンドの例外は `MyBot.on_app_command_error()` に集約する
- 未処理イベント例外は `MyBot.on_error()` で記録する
- `send_event_log()` はボット由来の操作ログに限定し、`source_channel_id` が統一ログチャンネルと一致する場合は送信しない
- 通常メッセージ由来の反応や監査ログは統一ログへ流さず、必要なものだけ `send_event_log()` 経由で通知する
- 外部依存の失敗は、可能であればフォールバックする
- Gemini の `generateContent` が 429 / クォータ超過になった場合は、`OLLAMA_FALLBACK_MODEL` と `ollama.model_chat` / `ollama.model_summary` を順に試して Ollama へ切り替える
- チャンネル固有の説明は `data/channel_rag/<channel_id>/chat_rag.md` に保存し、会話応答のローカル知識として参照する

## 10. ディレクトリ方針

### 10.1 現状

- 既存コードはルート直下の `ai/`, `cogs/`, `guards/`, `utils/`, `commands/` に分かれている
- エントリポイントは `bin/run.py`
- 設計書は `doc/` に配置する
- 主要な実行経路はルート直下のモジュールを前提にしている

### 10.2 今後の方針

- `src/` は将来の整理先として残し、移行する場合は段階的に行う
- 既存コードは無理に一括移行せず、変更対象に近い単位で段階移行する
- import パス、起動スクリプト、テスト手順を壊さないことを優先する
- チャンネル知識は `data/channel_rag/<channel_id>/chat_rag.md` を直接編集して追加する

### 10.3 想定移行イメージ

```mermaid
flowchart LR
    A[Current Root Modules] --> B[Gradual Migration Targets]
    B --> C[Shared Utilities Organized]
    C --> D[Gradual Import Path Cleanup]
    D --> E[Entry Point and Tests Updated]
```

## 11. 実装時の運用ルール

- 機能追加時は、必要なら `doc/feature-<name>.md` を追加する
- 大きな構成変更時は本書を更新する
- README には詳細設計を戻さず、参照リンクを置く
- 外部依存の増減があればセットアップ手順も更新する

## 12. 未整理事項

- `src/` への具体的な移行単位はまだ未確定
- テスト構成と CI 方針は別文書化していない
- AI 機能のプロンプト設計詳細は専用文書化されていない
