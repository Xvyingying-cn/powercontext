#!/usr/bin/env bash
# 初始化 PowerContext Scope（bash/curl 版，等价于 init-scope.py，不带 --seed）
# 用法: PC_URL=http://127.0.0.1:8000 ./init-scope.sh
set -euo pipefail

PC_URL="${PC_URL:-http://127.0.0.1:8000}"
PC_AUTH_HEADER="${PC_AUTH_HEADER:-}"

echo "[1/2] health check"
curl --fail --silent --show-error "$PC_URL/health/ready" && echo

echo "[2/2] create scope"
SCOPE_ID="$(curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  ${PC_AUTH_HEADER:+--header "$PC_AUTH_HEADER"} \
  --data '{"title":"pet-with-memory","summary":"Dify 桌宠小忆的长期记忆存储（Dify 赛题 pet-with-memory）","idempotency_key":"pet-with-memory-dify"}' \
  "$PC_URL/v1/scopes" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])')"

echo "SCOPE_ID=$SCOPE_ID"
echo "把它填入 Dify 应用环境变量 pc_scope_id"
