"""OpenAI-compatible chat client, standard library only.

Only one shape is needed: "give me a JSON object".  Everything else (retries,
timeouts, malformed output) degrades to ``None`` so the caller can fall back to
a heuristic instead of stalling a run.

Configuration comes from the environment so no key ever lands in the repo:

    DEEPSEEK_API_KEY   -> https://api.deepseek.com/v1, deepseek-chat
    OPENAI_API_KEY     -> https://api.openai.com/v1, gpt-4o-mini
    OPENAI_BASE_URL    -> override the endpoint
    LLM_MODEL          -> override the model name
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
    """Load simple KEY=VALUE lines into os.environ if not already set.

    Used for ``brain/.env`` (git-ignored) so the key never has to appear on a
    command line.  Deliberately tiny: no quotes, no interpolation, no exports
    beyond an optional leading ``export``.
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
    """Build a config, or None when no key is configured."""
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
        # Metadata of the most recent successful call, for the journal.
        self.last_meta: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> Optional["LLMClient"]:
        cfg = config_from_env()
        return cls(cfg) if cfg else None

    def json_chat(self, system: str, user: str) -> Optional[dict[str, Any]]:
        """Return a parsed JSON object, or None on any failure."""
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
    """A client that always answers with ``reply`` -- used by tests."""
    client = LLMClient(LLMConfig(base_url="http://mock", model="mock", api_key="mock"))
    client.json_chat = lambda system, user: reply  # type: ignore[method-assign]
    return client
