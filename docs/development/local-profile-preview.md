# Local Profile Preview

This document is for humans only.
It is intentionally kept outside the bot's local RAG inputs.

## Start the local API

```bash
python3 bin/profile_preview_server.py --host 127.0.0.1 --port 8089
```

## Health check

```bash
curl -sS http://127.0.0.1:8089/healthz
```

## Preview request

```bash
curl -sS -X POST http://127.0.0.1:8089/profile-preview \
  -H 'Content-Type: application/json' \
  -d '{
    "guild_id": 972052382315855912,
    "channel_id": 1493246078357606430,
    "scope": "auto",
    "question": "このサーバーはなにするところ？"
  }'
```

## Notes

- `scope`: `auto` / `guild` / `channel` / `legacy_channel`
- `emit_log: true` writes a JSONL management log
- `bin/profile_preview.py --json` runs the same logic without HTTP

## Debug route preview

`bin/debug_route.py` は、Bot のルーティングと応答生成をローカルで追うための補助コマンドです。

### Send-only dry run

送信だけ止めて、他はできるだけ本番経路に寄せたいときは `--dry-run-send` を使います。

```bash
python3 bin/debug_route.py mention \
  --mock-llm \
  --dry-run-send \
  --trace-llm \
  "このサーバーは何のやつ？"
```

### What it does

- ルート判定、履歴参照、プロンプト構築は通します
- AI / search は `--mock-llm` で差し替えます
- 実際の Discord への送信だけを抑止します
- 抑止された送信は出力に `[suppressed]` と表示されます

## One-shot call

```bash
python3 bin/profile_preview_call.py --json --guild-id 972052382315855912 --channel-id 1493246078357606430 --question "このサーバーはなにするところ？"
```

## One-command Ollama run

This starts `ollama serve`, pulls the model, and then runs the preview request.

```bash
python3 bin/profile_preview_ollama.py --json \
  --ollama-model llama3.2:1b \
  --guild-id 972052382315855912 \
  --channel-id 1493246078357606430 \
  --question "このサーバーはなにするところ？"
```

## Minimal run

This is the smallest practical invocation for the current profile preview flow.

```bash
echo "[ACTION] STEP=2 最小構成で実行"

python3 bin/profile_preview_ollama.py \
  --json \
  --guild-id 972052382315855912 \
  --channel-id 1493246078357606430 \
  --question 'このサーバーはなにするところ？'
```
