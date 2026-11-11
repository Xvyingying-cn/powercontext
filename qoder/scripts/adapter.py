#!/usr/bin/env python3
"""StudyCoach 本地适配器（CORS 转发层）

作用：浏览器页面（Qoder Sites 发布的静态站点）直接调用自部署 PowerContext 会因
缺少 CORS 头被拦截；本脚本在本地 8787 端口提供转发层：
    页面 -> adapter(8787, 带 CORS) -> PowerContext(默认 127.0.0.1:8000)

仅使用 Python 标准库，无需安装依赖：
    python scripts/adapter.py
可选环境变量：
    POWERCONTEXT_URL    默认 http://127.0.0.1:8000
    POWERCONTEXT_TOKEN  PowerContext 的 Bearer Token（服务开启鉴权时必填）
    ADAPTER_PORT        默认 8787
"""
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PC_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
PC_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "")
PORT = int(os.environ.get("ADAPTER_PORT", "8787"))

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self):
        for k, v in CORS.items():
            self.send_header(k, v)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = self.rfile.read(length) if length else b"{}"
            req = urllib.request.Request(
                PC_URL + self.path,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "studycoach-adapter/1.0",
                },
            )
            if PC_TOKEN:
                req.add_header("Authorization", "Bearer " + PC_TOKEN)
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                self.send_response(r.status)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
        except Exception as e:
            body = json.dumps({"error": "adapter: %s" % e}).encode()
            self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[adapter] %s\n" % (fmt % args))


def main():
    print("StudyCoach adapter")
    print("  PowerContext :", PC_URL)
    print("  Listening on : http://127.0.0.1:%d  (在网页设置里填入此地址)" % PORT)
    print("  Auth         :", "Bearer token configured" if PC_TOKEN else "no token (local dev)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
