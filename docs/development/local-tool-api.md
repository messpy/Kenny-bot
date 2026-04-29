# Local Tool API

Kenny-bot の tool 専用ローカル API です。

## Start

```bash
python3 bin/tool_api_server.py --host 127.0.0.1 --port 8090
```

## Endpoints

- `GET /healthz`
- `GET /tools`
- `POST /tool/serverinfo`
- `POST /tool/server_stats`
- `POST /tool/rag`
- `POST /tool/web_search`

## Design Notes

- LLM に直接 Discord の内部データを触らせず、読み取り系だけを tool API として切り出す
- `GET /tools` はそのまま LLM 向けの tool リファレンスとして使う
- まずは読み取り専用の tool を優先する
- 優先して増やす候補
  - `member_history`
  - `channel_history`
  - `reply_chain`
  - `member_profile`
  - `semantic_history`
  - `vrchat_world`
  - `bot_catalog`
  - `runtime_model`
- `serverinfo` と `rag` はサーバー/チャンネル固有情報の優先ソースにする
- `server_stats` はサーバー主、概算メンバー数、保存済みログ上の発言統計を返す
- `web_search` は最新情報のみで使い、サーバー説明やローカル知識の代替にはしない
- tool の入力は `guild_id` / `channel_id` / `user_id` / `query` のようにスコープを明示する
- `rag` には `capability_only` と `channel_only` がある。前者は機能説明向け、後者はそのチャンネル内の資料に限定したいときに使う
- planner は tool 目次だけを見て tool を選び、実行は API サーバー側で検証する

## Examples

### server_stats

```bash
curl -sS -X POST http://127.0.0.1:8090/tool/server_stats \
  -H 'Content-Type: application/json' \
  -d '{
    "guild_id": 972052382315855912,
    "channel_id": 1493246078357606430,
    "scope": "guild",
    "member_count": 128,
    "owner_name": "NEKO旅"
  }'
```

### serverinfo

```bash
curl -sS -X POST http://127.0.0.1:8090/tool/serverinfo \
  -H 'Content-Type: application/json' \
  -d '{
    "guild_id": 972052382315855912,
    "channel_id": 1493246078357606430,
    "scope": "auto",
    "question": "このサーバーはなにをするところ？"
  }'
```

### rag

```bash
curl -sS -X POST http://127.0.0.1:8090/tool/rag \
  -H 'Content-Type: application/json' \
  -d '{
    "guild_id": 972052382315855912,
    "channel_id": 1493246078357606430,
    "query": "VRC世界旅行",
    "limit": 3,
    "channel_only": true
  }'
```

### web_search

```bash
curl -sS -X POST http://127.0.0.1:8090/tool/web_search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "東京 今日 天気",
    "limit": 3
  }'
```
