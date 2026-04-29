# Kenny-bot メッセージ処理フロー

## 概要

```
Discord Message → on_message → 判定 → 処理分岐 → (AI応答 | リアクション) → 管理ログ
```

## 全体シーケンス

```mermaid
sequenceDiagram
    autonumber
    participant User as Discord User
    participant Discord as Discord API
    participant Bot as Kenny-bot
    participant Fetcher as MessageFetcher
    participant VectorStore as MessageVectorStore<br/>(embeddingのみ)
    participant Ollama as Ollama API
    participant FileLog as ファイルログ<br/>(ALL_EVENTS_LOG)
    participant LogChannel as 管理ログch<br/>(1005826751391342663)

    User->>Discord: メッセージ送信
    Discord->>Bot: on_message()

    rect rgb(240, 248, 255)
        Note over Bot: 前提チェック
        alt Bot自身 or Webhook
            Bot->>Discord: 無視 (return)
        end
        alt DM
            Bot->>Bot: _handle_dm_message()
            Bot->>FileLog: log_user_message()
            Bot->>Discord: AI応答
            Bot->>FileLog: log_ai_output()
        end
        alt Spam検出
            Bot->>Bot: _handle_spam_violation()
            Bot->>Discord: 警告/ブロック
        end
        alt kenny-chat
            Bot->>Bot: _handle_kenny_chat_bridge()
            Bot->>Discord: 中継メッセージ
        end
    end

    Bot->>Bot: メンション/リプライ判定
    Note over Bot: mentioned_bot<br/>is_reply_to_bot<br/>recent_mention_window<br/>should_treat_as_mention

    alt NOT should_treat_as_mention (リアクションのみ)
        Bot->>VectorStore: _schedule_message_index()
        Bot->>Bot: キーワードリアクション (絵文字)
        Bot->>FileLog: send_event_log(send_discord=False)
    end

    alt should_treat_as_mention (AI応答)
        Bot->>Bot: 特殊コマンド処理
        Note over Bot: 議事録 start/stop<br/>機能説明クエリ<br/>モデル情報

        Bot->>Bot: SpamGuard.allow_ai()
        alt レート制限中
            Bot-->>User: 制限中メッセージ
            Bot->>Discord: process_commands()
        end

        Bot->>VectorStore: _schedule_message_index()

        Bot->>Bot: _resolve_chat_context()
        Bot->>Fetcher: fetch_recent() / fetch_user_recent()
        Fetcher->>Discord: channel.history()
        Discord-->>Fetcher: discord.Message[]
        Fetcher-->>Bot: history_context

        Bot->>Bot: _needs_web_search_for_accuracy()
        alt 最新情報が必要
            Bot-->>User: 最新情報の検索失敗
            Bot->>FileLog: send_event_log(send_discord=False)
            Bot->>Discord: process_commands()
        end

        Bot->>Ollama: _run_ollama_chat_with_tools()
        Note over Ollama: ツール呼び出し:<br/>get_local_knowledge<br/>search_vrchat_world<br/>web_search<br/>web_fetch
        Ollama-->>Bot: answer + references

        alt web_followup
            Bot->>Bot: _rewrite_answer_with_web()
        end

        Bot->>Discord: msg.channel.send()
        Discord-->>User: 応答メッセージ
    end

    alt DM
        Bot->>FileLog: log_ai_output()
    end

    alt should_treat_as_mention
        Bot->>Bot: _log_bot_activity_event()
        Bot->>FileLog: ALL_EVENTS_LOG.write()
        Bot->>Discord: send_event_log()
        Discord-->>LogChannel: Bot 管理ログ
    end

    Bot->>Discord: process_commands()
```

## on_message 詳細

```mermaid
flowchart TD
    subgraph 入力
        MSG[discord.Message]
    end

    subgraph 前提チェック
        IS_BOT[Bot自身?\nmsg.author.id == bot.user.id]
        IS_WEBHOOK[Webhook?\nmsg.webhook_id != None]
        IS_DM[DM?\nmsg.guild is None]
        IS_SPAM[Spam?\nSpamGuard.allow_message]
        IS_KENNY_CHAT[kenny-chat?\n_is_kenny_chat]
    end

    subgraph 判定
        MENTIONED[メンション?\nmentioned_bot]
        REPLY[リプライ?\nis_reply_to_bot]
        RECENT_WIN[直近メンション?\n_has_recent_mention_window]
        SHOULD_AI[should_treat_as_mention]
    end

    subgraph リアクション分岐
        REACTION[リアクションのみ]
        AI_RESP[AI応答処理]
    end

    MSG --> IS_BOT
    IS_BOT -->|Yes| RETURN[return]
    IS_BOT -->|No| IS_WEBHOOK
    IS_WEBHOOK -->|Yes| RETURN
    IS_WEBHOOK -->|No| IS_DM
    IS_DM -->|Yes| DM[DM処理\n_handle_dm_message]
    IS_DM -->|No| IS_KENNY_CHAT
    IS_KENNY_CHAT -->|Yes| KENNY[跨サーバー中継\n_handle_kenny_chat_bridge]
    IS_KENNY_CHAT -->|No| IS_SPAM
    KENNY --> CMDS[process_commands]

    IS_SPAM -->|No| PASS[ok]
    IS_SPAM -->|Yes| SPAM[スパム処理\n_handle_spam_violation]
    SPAM --> CMDS

    PASS --> MENTIONED
    MENTIONED --> REPLY
    REPLY --> RECENT_WIN
    RECENT_WIN --> SHOULD_AI

    SHOULD_AI -->|false| REACTION
    SHOULD_AI -->|true| AI_RESP

    REACTION --> INDEX[embedding登録\n_schedule_message_index]
    REACTION --> KEYWORD[キーワードリアクション]
    KEYWORD --> LOG_REACT[send_event_log<br/>send_discord=False]
    LOG_REACT --> CMDS

    AI_RESP --> SPECIAL[特殊コマンド?\n議事録 start/stop\n機能説明クエリ\nモデル情報]
    SPECIAL -->|Yes| SPECIAL_RESP[対応処理]
    SPECIAL --> LOG_SPEC[send_event_log]
    LOG_SPEC --> CMDS
    SPECIAL -->|No| RATE[レート制限\nSpamGuard]
    RATE -->|blocked| RATE_MSG[制限中メッセージ]
    RATE -->|ok| INDEX

    INDEX --> CTX[_resolve_chat_context]
    CTX --> NEEDS_WEB[_needs_web_search]
    NEEDS_WEB -->|Yes| WEB_FAIL[Web検索失敗応答]
    NEEDS_WEB -->|No| OLLAMA[_run_ollama_chat_with_tools]
    WEB_FAIL --> LOG_WEB[send_event_log]
    WEB_FAIL --> CMDS

    OLLAMA --> LOG_AI[log_ai_output]
    OLLAMA --> SEND[msg.channel.send]
    SEND --> LOG[_log_bot_activity_event]
    LOG --> CMDS
```

## _handle_dm_message 詳細

```mermaid
sequenceDiagram
    participant User as Discord User
    participant Bot as Kenny-bot
    participant Fetcher as MessageFetcher
    participant Ollama as Ollama API
    participant FileLog as ファイルログ

    User->>Bot: DM送信
    Bot->>FileLog: log_user_message()

    Bot->>Bot: 特殊クエリ?<br/>_is_runtime_model_query<br/>_is_capability_query
    alt 特殊クエリ
        Bot->>Bot: 対応処理
        Bot->>User: 応答
    end

    Bot->>Bot: _is_ai_channel_rate_limited()
    alt 制限中
        Bot-->>User: 制限中メッセージ
    end

    Bot->>Bot: _schedule_message_index()
    Bot->>Bot: _resolve_chat_context()
    Bot->>Fetcher: fetch_recent()
    Fetcher-->>Bot: history_context

    Bot->>Ollama: _run_ollama_chat_with_tools()
    Ollama-->>Bot: answer

    Bot->>Bot: log_ai_output()
    Bot->>Bot: _log_bot_activity_event()
    Bot->>Bot: msg.channel.send()
    Bot-->>User: 応答
```

## _resolve_chat_context 詳細

```mermaid
flowchart TD
    subgraph 入力
        MSG[discord.Message]
        TEXT[メッセージ本文]
        USER_DISPLAY[ユーザー表示名]
    end

    subgraph 設定値
        UL[user_lines: 24\nchat.user_history_lines]
        CL[channel_lines: 16\nchat.channel_history_lines]
    end

    subgraph 取得計画
        PLAN[_build_retrieval_plan]
        PRIORITY[_prioritize_mentioned_person_plan]
    end

    subgraph source種類
        RECENT_USER[recent_user_history]
        MEMBER_HIST[member_history]
        RECENT_TURNS[recent_turns]
        CHANNEL_HIST[channel_history]
        REPLY_CHAIN[reply_chain]
        SEMANTIC[semantic_history]
        LOCAL_KNOW[local_knowledge]
        MEMBER_PROFILE[member_profile]
        CHANNEL_PROFILE[channel_profile]
        WEB[web_search]
        BOT_COMMAND[bots_command_catalog]
        BOT_GAME[bot_game_catalog]
        RUNTIME_MODEL[runtime_model]
        VRCHAT[vrchat_world]
    end

    subgraph Fetcher
        FETCH[MessageFetcher]
        HISTORY[Discord API\nchannel.history()]
        CACHE[(OrderedDict\n30秒TTL)]
    end

    subgraph VectorStore
        VS[MessageVectorStore]
        EMBEDDING[SQLite\nembedding検索]
    end

    subgraph ローカルRAG
        LRAG[LocalRAG\n_get_local_knowledge]
    end

    subgraph 出力
        CONTEXT[history_context文字列]
        REFS[references list]
        WEB_Q[web_queries list]
        DETAILS[reference_details list]
    end

    MSG --> PLAN
    TEXT --> PLAN
    USER_DISPLAY --> PLAN
    UL --> RECENT_USER
    UL --> MEMBER_HIST
    CL --> RECENT_TURNS
    CL --> CHANNEL_HIST

    PLAN --> PRIORITY
    PRIORITY --> RECENT_USER
    PRIORITY --> MEMBER_HIST
    PRIORITY --> RECENT_TURNS
    PRIORITY --> CHANNEL_HIST
    PRIORITY --> REPLY_CHAIN
    PRIORITY --> SEMANTIC
    PRIORITY --> LOCAL_KNOW
    PRIORITY --> MEMBER_PROFILE
    PRIORITY --> CHANNEL_PROFILE
    PRIORITY --> WEB
    PRIORITY --> BOT_COMMAND
    PRIORITY --> BOT_GAME
    PRIORITY --> RUNTIME_MODEL
    PRIORITY --> VRCHAT

    RECENT_USER --> FETCH
    MEMBER_HIST --> FETCH
    RECENT_TURNS --> FETCH
    CHANNEL_HIST --> FETCH
    REPLY_CHAIN --> FETCH

    FETCH --> CACHE
    FETCH --> HISTORY
    HISTORY -->|discord.Message[]| FORMAT[format_messages_for_context]
    FORMAT --> CONTEXT

    SEMANTIC --> FETCH_EMBED[_embed_text]
    FETCH_EMBED --> EMBEDDING
    EMBEDDING --> FORMAT_VS[format_results]
    FORMAT_VS --> CONTEXT

    LOCAL_KNOW --> LRAG
    LRAG --> CONTEXT

    VRCHAT --> VRAPI[search_vrchat_world]
    VRAPI --> CONTEXT
```

## MessageFetcher 詳細

```mermaid
sequenceDiagram
    participant Caller as Caller
    participant Fetcher as MessageFetcher
    participant Cache as OrderedDict Cache
    participant Discord as Discord API

    Caller->>Fetcher: fetch_recent(channel, count, use_cache=True)
    Fetcher->>Cache: _get_cached(channel_id)

    alt Cache HIT & len >= count
        Cache-->>Fetcher: messages
        Fetcher-->>Caller: cached messages
    end

    alt Cache MISS or len < count
        Fetcher->>Discord: channel.history(limit=count*3)
        Discord-->>Fetcher: messages
        Fetcher->>Cache: _set_cached(channel_id, messages)
        Fetcher-->>Caller: fresh messages
    end

    Caller->>Fetcher: fetch_user_recent(channel, user_id, count, search_limit=500)
    Fetcher->>Discord: channel.history(limit=search_limit)
    loop メッセージ走査
        Discord->>Discord: filter by author_id == user_id
    end
    Discord-->>Fetcher: filtered messages
    Fetcher-->>Caller: user messages

    Note over Fetcher: fetch_user_recentは<br/>キャッシュなし
```

## ログ記録の詳細

```mermaid
sequenceDiagram
    participant Handler as Handler
    participant EventLog as send_event_log()
    participant FileLog as ALL_EVENTS_LOG
    participant Discord as Discord API

    Handler->>EventLog: send_event_log()
    EventLog->>FileLog: ALL_EVENTS_LOG.write()
    EventLog->>Discord: channel.send(embed)

    Note over EventLog: fields:<br/>- kind (メンション/DM等)<br/>- processing<br/>- 送信者<br/>- 場所<br/>- メッセージID<br/>- 入出力テキスト<br/>- モデル名<br/>- Web検索有無<br/>- 参照URL

    Handler->>FileLog: log_user_message(msg)
    Handler->>FileLog: log_ai_output()
```

## 状態遷移

```mermaid
stateDiagram-v2
    [*] --> 前提チェック
    前提チェック --> DM処理: msg.guild is None
    前提チェック --> kenny-chat: _is_kenny_chat
    前提チェック --> Spam処理: SpamGuard
    前提チェック --> continue: OK

    DM処理 --> log_user_message
    DM処理 --> 特殊クエリ判定
    DM処理 --> レート制限
    DM処理 --> embedding登録
    DM処理 --> コンテキスト解決
    DM処理 --> Ollama応答
    DM処理 --> log_ai_output
    DM処理 --> 管理ログ記録
    DM処理 --> [*]

    kenny-chat --> 中継処理
    kenny-chat --> [*]

    Spam処理 --> [*]

    continue --> メンション判定
    メンション判定 --> リアクション: not should_treat_as_mention
    メンション判定 --> 特殊処理: should_treat_as_mention

    特殊処理 --> 特殊応答: 議事録/機能説明
    特殊応答 --> 管理ログ記録
    特殊応答 --> [*]

    特殊処理 --> レート制限
    レート制限 --> 制限中: blocked
    レート制限 --> continue2: ok
    制限中 --> [*]

    continue2 --> Embedding登録
    continue2 --> Web検索判定
    Web検索判定 --> Web失敗: 必要
    Web失敗 --> [*]
    Web検索判定 --> AI応答: 不要

    リアクション --> Embedding登録
    リアクション --> キーワードリアクション
    リアクション --> [*]

    AI応答 --> コンテキスト解決
    コンテキスト解決 --> Ollama呼び出し
    Ollama呼び出し --> 応答送信
    応答送信 --> 管理ログ記録
    管理ログ記録 --> [*]
```

## 主要ファイル対応表

| ファイル | 役割 |
|---------|------|
| `cogs/message_logger.py` | メイン処理ロジック (on_message, _handle_dm_message等) |
| `cogs/slash_commands.py` | スラッシュコマンド処理 |
| `cogs/game_commands.py` | ゲームコマンド |
| `cogs/voice_logger.py` | 音声ログ |
| `cogs/member_logger.py` | メンバーログ |
| `cogs/audit_logger.py` | 監査ログ |
| `cogs/reaction_roles.py` | リアクションロール |
| `cogs/tts_reader.py` | TTS読み上げ |
| `cogs/mod_panel.py` | モデレーションパネル |
| `utils/message_fetcher.py` | Discord API (channel.history) + 短期キャッシュ |
| `utils/message_vector_store.py` | embedding保存・検索 (SQLite) |
| `utils/event_logger.py` | 管理ログ送信 (send_event_log) |
| `utils/message_logger.py` | ファイルログ (log_user_message, log_ai_output等) |
| `utils/local_rag.py` | ローカルナレッジベース |
| `utils/spam_guard.py` | スパム検出・レート制限 |

## 設定値

| キー | デフォルト | 説明 |
|------|-----------|------|
| `chat.user_history_lines` | 24 | ユーザー履歴取得件数 |
| `chat.channel_history_lines` | 16 | チャンネル履歴取得件数 |
| `chat.semantic_history_k` | 6 | セマンティック検索件数 |
| `chat.max_response_length` | 1800 | 応答最大文字数 |
| `security.max_user_message_chars` | 1200 | ユーザーメッセージ最大文字数 |
| `security.ai_channel_cooldown_seconds` | 4 | AI応答間隔制限 |

## 削除した機能 (v2)

| 項目 | 旧方式 | 新方式 |
|------|--------|--------|
| メッセージ保存 | MessageStore → JSONファイル | なし |
| 履歴取得 | store.get_recent_messages() | MessageFetcher.fetch_recent() |
| 送信記録 | store.add_message() | 削除 (embeddingのみでOK) |
| Storage | data/message_logs/*.json | なし |

## 変更なし (維持)

| 項目 | 説明 |
|------|------|
| log_user_message() | ファイルログ (ALL_EVENTS_LOG) |
| log_ai_output() | ファイルログ (AI応答記録) |
| send_event_log() | Discord管理ログ + ファイルログ |
| _log_bot_activity_event() | 管理ログEmbed生成 |
| _schedule_message_index() | embedding登録 (バックグラウンド) |