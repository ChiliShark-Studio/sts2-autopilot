"""回放日志: 按顺序记录每次决策, 保留重新推导所需的上下文.

参考项目在 ``runs/<seed>/`` 中记录状态, 决策, LLM 调用和搜索记录.
这里采用更简单的方式: 每步写入一条 JSONL, 包含原始协议状态, 路由请求和决策.

这些记录用于:
  * 使用修改后的策略离线重放历史状态.
  * 在相同状态上比较两种策略.
  * 不重开游戏即可定位一次错误决策.

保存原始协议状态而非类型化对象, 使 C# 也能读取同一份日志.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .contracts import Decision, DecisionRequest
from .model import GameState


@dataclass
class Journal:
    path: Path
    step: int = 0

    @classmethod
    def create(cls, directory: Path) -> "Journal":
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory / f"run-{datetime.now():%Y%m%d-%H%M%S}.jsonl")

    def record(self, state: GameState, decision: Decision, request: Optional[DecisionRequest] = None,
               elapsed_ms: int = 0) -> None:
        self.step += 1
        entry = {
            "step": self.step,
            "screen": state.screen.value,
            "state": state.raw,
            "request": request.to_wire() if request else None,
            "decision": decision.to_wire(),
            "elapsed_ms": elapsed_ms,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def replay_states(path: Path) -> Iterator[GameState]:
    """按顺序返回历史对局的状态, 供策略重新决策."""
    for entry in Journal(path):
        yield GameState.parse(entry["state"])


def llm_decisions(path: Path) -> Iterator[dict[str, Any]]:
    """筛选由模型作出决策的记录, 包含对应的提示词和回复."""
    for entry in Journal(path):
        decision = entry.get("decision") or {}
        if decision.get("source") == "llm":
            yield entry


def summarize(path: Path) -> dict[str, Any]:
    entries = list(Journal(path))
    screens: dict[str, int] = {}
    sources: dict[str, int] = {}
    tools: dict[str, int] = {}
    for entry in entries:
        screens[entry["screen"]] = screens.get(entry["screen"], 0) + 1
        decision = entry.get("decision") or {}
        sources[decision.get("source", "?")] = sources.get(decision.get("source", "?"), 0) + 1
        tools[decision.get("tool", "?")] = tools.get(decision.get("tool", "?"), 0) + 1
    return {"steps": len(entries), "screens": screens, "sources": sources, "tools": tools}
