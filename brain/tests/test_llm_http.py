"""使用本地模拟服务对 LLM 传输层进行端到端测试.

无需密钥或外部网络, 验证请求形状, JSON 模式解析和失败路径.
这些环节在实际运行中容易静默失败.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brain.llm import LLMClient, LLMConfig

GOOD_BODY = {
    "model": "test-model",
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    "choices": [{"message": {"content": json.dumps({"choice": 1, "reason": "测试"})}}]
}


class _Handler(BaseHTTPRequestHandler):
    status = 200
    body: dict = GOOD_BODY
    seen: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Handler.seen["payload"] = json.loads(self.rfile.read(length).decode("utf-8"))
        _Handler.seen["auth"] = self.headers.get("Authorization")
        _Handler.seen["path"] = self.path
        data = json.dumps(_Handler.body).encode("utf-8")
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 抑制测试日志输出
        pass


class LLMTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _client(self, retries: int = 0) -> LLMClient:
        return LLMClient(LLMConfig(
            base_url=f"http://127.0.0.1:{self.port}/v1",
            model="test-model",
            api_key="sk-test",
            timeout=5,
            retries=retries,
        ))

    def test_parses_json_content(self):
        client = self._client()
        reply = client.json_chat("system", "user")
        self.assertEqual(reply, {"choice": 1, "reason": "测试"})
        self.assertEqual(_Handler.seen["path"], "/v1/chat/completions")
        self.assertEqual(_Handler.seen["auth"], "Bearer sk-test")
        self.assertEqual(_Handler.seen["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(_Handler.seen["payload"]["model"], "test-model")

    def test_captures_metadata_for_the_journal(self):
        client = self._client()
        client.json_chat("system", "user prompt")
        self.assertEqual(client.last_meta["model"], "test-model")
        self.assertEqual(client.last_meta["usage"]["prompt_tokens"], 11)
        self.assertEqual(client.last_meta["prompt"], "user prompt")
        self.assertIn("reason", client.last_meta["reply"])

    def test_http_error_returns_none(self):
        _Handler.status = 500
        try:
            self.assertIsNone(self._client().json_chat("system", "user"))
        finally:
            _Handler.status = 200

    def test_malformed_content_returns_none(self):
        _Handler.body = {"choices": [{"message": {"content": "not json"}}]}
        try:
            self.assertIsNone(self._client().json_chat("system", "user"))
        finally:
            _Handler.body = GOOD_BODY


if __name__ == "__main__":
    unittest.main()
