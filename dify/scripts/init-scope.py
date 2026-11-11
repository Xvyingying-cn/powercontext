#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化 PowerContext Scope，为「桌宠小忆」创建数据分区并预置初始记忆。

用法：
    python init-scope.py                       # 只创建 scope，打印 SCOPE_ID
    python init-scope.py --seed                # 创建 scope 并预置示例记忆
    python init-scope.py --url http://host:8000 --token xxx

依赖：仅 Python 3.8+ 标准库。返回的 SCOPE_ID 需要填入 Dify 应用的
环境变量 pc_scope_id（见 README.md 第 4 节）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8000"

# 预置的示例记忆：演示 PowerContext 侧真实写入（kind 为自由字符串）。
SEED_MEMORIES = [
    ("preference", "主人小徐最喜欢的菜是西红柿炒蛋，讨厌香菜。"),
    ("preference", "主人习惯在晚上 10 点后摸鱼，小忆这时要提醒主人休息。"),
    ("fact", "小忆的生日定在每年的 11 月 11 日，主人说要给它庆祝。"),
]


def post(path: str, payload: dict[str, Any], base_url: str, token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PowerContext {path} -> HTTP {error.code}: {detail}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="init PowerContext scope for pet-with-memory")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="PowerContext Server 地址")
    parser.add_argument("--token", default="", help="Bearer token（未启用鉴权时留空）")
    parser.add_argument("--seed", action="store_true", help="预置示例记忆")
    args = parser.parse_args()

    base_url, token = args.url.strip(), args.token.strip() or None

    # 1. 健康检查
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health/ready", timeout=10) as r:
            print(f"[1/3] health/ready -> HTTP {r.status} {r.read().decode('utf-8', 'replace')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[1/3] Server 不可达：{exc}\n请先运行: powercontext server run", file=sys.stderr)
        return 1

    # 2. 创建 scope
    scope = post(
        "/v1/scopes",
        {
            "title": "pet-with-memory",
            "summary": "Dify 桌宠小忆的长期记忆存储（Dify 赛题 pet-with-memory）",
            "idempotency_key": "pet-with-memory-dify",
        },
        base_url, token,
    )
    scope_id = scope["scope_id"]
    print(f"[2/3] scope created -> {json.dumps(scope, ensure_ascii=False)}")

    # 3. 预置记忆（可选）
    if args.seed:
        for kind, text in SEED_MEMORIES:
            saved = post(
                "/v1/memory/remember",
                {"scope_id": scope_id, "kind": kind, "text": text,
                 "reason": "seeded by scripts/init-scope.py"},
                base_url, token,
            )
            n = len(saved.get("records", []))
            print(f"[3/3] remembered({kind}) -> {n} record(s): {text}")
    else:
        print("[3/3] 跳过预置记忆（未加 --seed）")

    print("\n✅ 完成。把下面的值填入 Dify 应用环境变量 pc_scope_id：")
    print(f"SCOPE_ID={scope_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
