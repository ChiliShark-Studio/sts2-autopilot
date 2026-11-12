"""仅使用标准库的 OpenAI 兼容聊天客户端.

只支持返回 JSON 对象的调用方式. 重试失败, 超时或输出格式错误时返回 ``None``,
让调用方退回启发式策略, 避免阻塞整局游戏.

配置从环境变量读取, 避免将密钥写入仓库:

    DEEPSEEK_API_KEY   -> https://api.deepseek.com/v1, deepseek-chat
    OPENAI_API_KEY     -> https://api.openai.com/v1, gpt-4o-mini
    OPENAI_BASE_URL    -> 覆盖接口地址
    LLM_MODEL          -> 覆盖模型名称
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout: float = 30.0
    retries: int = 2
    temperature: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.model} @ {self.base_url}"


def _load_env_file(path: "os.PathLike[str] | str") -> None:
    """将简单的 KEY=VALUE 配置加载到 os.environ, 不覆盖已存在的变量.

    用于读取已被 Git 忽略的 ``brain/.env``, 避免在命令行中出现密钥.
    实现刻意保持简单: 不处理引号或插值, 仅支持可选的前导 ``export``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def config_from_env() -> Optional[LLMConfig]:
    """构建配置, 未配置密钥时返回 None."""
    _load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

    if key := os.environ.get("DEEPSEEK_API_KEY"):
        return LLMConfig(
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_DEEPSEEK_BASE),
            model=os.environ.get("LLM_MODEL", "deepseek-chat"),
            api_key=key,
        )
    if key := os.environ.get("OPENAI_API_KEY"):
        return LLMConfig(
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE),
            model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            api_key=key,
        )
    if key := os.environ.get("LLM_API_KEY"):
        return LLMConfig(
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_DEEPSEEK_BASE),
            model=os.environ.get("LLM_MODEL", "deepseek-chat"),
            api_key=key,
        )
    return None


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.last_error: Optional[str] = None
        # 最近一次成功调用的元数据, 用于写入日志.
        self.last_meta: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> Optional["LLMClient"]:
        cfg = config_from_env()
        return cls(cfg) if cfg else None

    def json_chat(self, system: str, user: str) -> Optional[dict[str, Any]]:
        """返回解析后的 JSON 对象, 任何失败均返回 None."""
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self.config.base_url.rstrip("/") + "/chat/completions"

        for attempt in range(self.config.retries + 1):
            started = time.perf_counter()
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {self.config.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError(f"expected object, got {type(parsed).__name__}")
                self.last_meta = {
                    "model": data.get("model") or self.config.model,
                    "usage": data.get("usage"),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "prompt": user,
                    "reply": content,
                }
                return parsed
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                    TimeoutError, ValueError, OSError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.config.retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                print(f"[llm] giving up after {attempt + 1} attempts: {self.last_error}",
                      file=sys.stderr)
                return None
        return None


def make_mock_client(reply: dict[str, Any]) -> LLMClient:
    """构建始终返回 ``reply`` 的客户端, 供测试使用."""
    client = LLMClient(LLMConfig(base_url="http://mock", model="mock", api_key="mock"))
    client.json_chat = lambda system, user: reply  # type: ignore[method-assign]
    return client
