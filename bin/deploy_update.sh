#!/usr/bin/env bash
set -euo pipefail

repo_dir="${1:-/home/kennypi/deploy/Kenny-bot}"
remote="${GIT_REMOTE:-gitlab}"
branch="${GIT_BRANCH:-dev}"
service_name="${SERVICE_NAME:-kennybot.service}"
uv_bin="${UV_BIN:-/home/kennypi/.local/bin/uv}"

cd "$repo_dir"
git fetch "$remote" "$branch"
git reset --hard FETCH_HEAD
git submodule sync --recursive
git submodule update --init --recursive --force
"$uv_bin" sync --frozen
systemctl --user daemon-reload
systemctl --user restart "$service_name"
systemctl --user is-active "$service_name"
