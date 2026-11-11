"""Bridge transport: the only place that speaks HTTP and the mod's protocol."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from .actions import Action
from .model import GameState

DEFAULT_BASE = "http://127.0.0.1:15526"


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 15.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def state(self) -> GameState:
        return GameState.parse(self._get("/api/v1/singleplayer"))

    def act(self, action: Action, state: Optional[GameState] = None) -> dict[str, Any]:
        """Validate against the current state, then send.

        Validation is done here rather than in the policy so that *every*
        caller -- agent, tests, a REPL -- gets the same protection against
        stale indices.
        """
        current = state or self.state()
        action.check(current)
        reply = self._post("/api/v1/singleplayer", action.to_payload())
        if reply.get("status") != "ok":
            raise BridgeError(f"{action.describe()} rejected: {reply.get('error')}")
        return reply

    # -- low level --------------------------------------------------------- #
    def _get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(self.base + path, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def healthy(self) -> bool:
        try:
            req = urllib.request.Request(self.base + "/", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False
