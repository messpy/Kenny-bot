#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# uv を解決（ホストでは ~/.local/bin/uv、コンテナでは PATH 上を想定）
UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  if [ -x "/home/kennypi/.local/bin/uv" ]; then
    UV_BIN="/home/kennypi/.local/bin/uv"
  else
    UV_BIN="$(command -v uv || true)"
  fi
fi
if [ -z "$UV_BIN" ] || [ ! -x "$UV_BIN" ]; then
  echo "uv not found" >&2
  exit 127
fi

VOICEVOX_URL="${VOICEVOX_URL:-}"
START_VOICEVOX_SH="$SCRIPT_DIR/bin/start_voicevox.sh"
if [ -z "$VOICEVOX_URL" ] && [ -x "$START_VOICEVOX_SH" ]; then
  if voicevox_url="$("$START_VOICEVOX_SH" 2>/dev/null)"; then
    export VOICEVOX_URL="$voicevox_url"
    echo "VOICEVOX_URL=$VOICEVOX_URL"
  fi
fi

exec "$UV_BIN" run bin/run.py
