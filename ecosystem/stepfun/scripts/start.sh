#!/usr/bin/env bash
# start.sh — 启动本地桥接服务（托管前端 + 嵌入式 seekdb）
# 启动后浏览器访问 http://127.0.0.1:8788
set -euo pipefail
cd "$(dirname "$0")/../server"
export PORT="${PORT:-8788}"
echo "[start] http://127.0.0.1:${PORT}  （数据目录 server/../data/seekdb-data）"
exec node server.mjs
