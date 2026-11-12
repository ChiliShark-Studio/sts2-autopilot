"""模拟 OpenAI 兼容接口, 无需密钥即可测试 LLM 调用链.

    python tools/mock_llm.py 18777 1     # 始终返回 {"choice": 1, ...}

然后配置并调用:
    $env:OPENAI_BASE_URL = "http://127.0.0.1:18777/v1"
    $env:LLM_API_KEY     = "mock"
    python -m brain.run_map --explain
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18777
CHOICE = int(sys.argv[2]) if len(sys.argv) > 2 else 0
REPLY = {"choice": CHOICE, "reason": "mock：血量健康，走普通怪积累卡组"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(length)
        content = json.dumps(REPLY, ensure_ascii=False)
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    print(f"mock llm on 127.0.0.1:{PORT}, always choosing {CHOICE}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
