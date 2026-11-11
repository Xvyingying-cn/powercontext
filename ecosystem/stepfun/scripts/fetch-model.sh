#!/usr/bin/env bash
# fetch-model.sh — 拉取端侧 embedding 模型（ONNX + tokenizer）到 web/models/
# 模型体积较大（约 145 MB），不进 git，运行前执行本脚本即可。
set -euo pipefail

BASE="https://hf-mirror.com/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main"
DEST="$(cd "$(dirname "$0")/.." && pwd)/web/models/Xenova/paraphrase-multilingual-MiniLM-L12-v2"

mkdir -p "$DEST/onnx"
for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json unigram.json; do
  if [ ! -s "$DEST/$f" ]; then
    echo "[fetch] $f"
    curl -fL --retry 3 -o "$DEST/$f" "$BASE/$f"
  fi
done
if [ ! -s "$DEST/onnx/model_quantized.onnx" ]; then
  echo "[fetch] onnx/model_quantized.onnx（约 113 MB，耐心等待）"
  curl -fL --retry 3 -o "$DEST/onnx/model_quantized.onnx" "$BASE/onnx/model_quantized.onnx"
fi
echo "[fetch] 完成：$(du -sh "$DEST" | cut -f1) @ $DEST"
