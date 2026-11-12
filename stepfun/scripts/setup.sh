#!/usr/bin/env bash
# setup.sh — 在 Linux / WSL2 / macOS 上准备运行环境（安装 Node 22 与依赖）
set -euo pipefail

cd "$(dirname "$0")/.."

need_node=1
if command -v node >/dev/null 2>&1; then
  major=$(node -p "process.versions.node.split('.')[0]")
  if [ "$major" -ge 22 ]; then need_node=0; fi
fi

if [ "$need_node" -eq 1 ]; then
  echo "[setup] 安装 Node.js 22（seekdb-node 要求 >=22）..."
  if command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
  else
    echo "[setup] 未识别的包管理器，请手动安装 Node.js 22 后重试" >&2
    exit 1
  fi
fi

echo "[setup] Node $(node -v) / npm $(npm -v)"
cd server
echo "[setup] 安装依赖（含 seekdb 原生运行时，约 200MB）..."
npm install --no-audit --no-fund
echo "[setup] 拉取端侧模型（本地缓存到 web/models/）..."
bash ../scripts/fetch-model.sh
echo "[setup] 完成。启动：bash ../scripts/start.sh"
