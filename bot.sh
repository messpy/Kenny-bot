#!/usr/bin/env bash
set -Eeuo pipefail
cd /home/kennypi/work/kennybot

# uv の絶対パス（存在しなければ即終了）
UV_BIN="/home/kennypi/.local/bin/uv"
if [ ! -x "$UV_BIN" ]; then
  echo "uv not found: $UV_BIN" >&2
  exit 127
fi

exec "$UV_BIN" run bin/run.py
