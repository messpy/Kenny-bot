#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE="${VOICEVOX_IMAGE:-voicevox/voicevox_engine:cpu-latest}"
CONTAINER_NAME="${VOICEVOX_CONTAINER_NAME:-kennybot-voicevox}"
HOST="${VOICEVOX_HOST:-127.0.0.1}"
BASE_PORT="${VOICEVOX_PORT_BASE:-50121}"
MAX_TRIES="${VOICEVOX_PORT_TRIES:-20}"

is_http_ready() {
  local port="$1"
  curl -fsS -m 2 "http://${HOST}:${port}/version" >/dev/null 2>&1
}

is_port_busy() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :${port} )" 2>/dev/null | grep -q ":${port}"
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  return 1
}

if ! command -v docker >/dev/null 2>&1; then
  echo "VOICEVOX auto-start skipped: docker not found" >&2
  exit 0
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${CONTAINER_NAME}"; then
  port="$(docker port "${CONTAINER_NAME}" 50021/tcp 2>/dev/null | tail -n 1 | sed 's/.*://')"
  if [ -n "${port:-}" ] && is_http_ready "$port"; then
    echo "http://${HOST}:${port}"
    exit 0
  fi
fi

chosen_port=""
i=0
while [ "$i" -lt "$MAX_TRIES" ]; do
  port=$((BASE_PORT + i))
  if ! is_port_busy "$port"; then
    chosen_port="$port"
    break
  fi
  i=$((i + 1))
done

if [ -z "$chosen_port" ]; then
  echo "VOICEVOX auto-start skipped: no free port in ${BASE_PORT}..$((BASE_PORT + MAX_TRIES - 1))" >&2
  exit 0
fi

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${HOST}:${chosen_port}:50021" \
  "${IMAGE}" >/dev/null

j=0
while [ "$j" -lt 30 ]; do
  if is_http_ready "$chosen_port"; then
    echo "http://${HOST}:${chosen_port}"
    exit 0
  fi
  sleep 1
  j=$((j + 1))
done

echo "VOICEVOX auto-start failed on http://${HOST}:${chosen_port}" >&2
exit 1
