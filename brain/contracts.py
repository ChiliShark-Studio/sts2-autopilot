"""决策契约: 所有 Agent 和工具共享的接口.

这里使用简单的 JSON 形状, 便于将整个决策层移植到 C# Mod.
所有结构都应能表示为普通字典, 让 C# 实现无需转换即可生成和读取:

    DecisionRequest  { kind, scene, state, legal }
    Decision         { action, reason, source }

本文件不处理 HTTP, 文件或时钟等外部交互.
字段必须能够通过 ``json.dumps`` 序列化.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .actions import Action
from .model import GameState


class DecisionKind(str, Enum):
    """决策归属, 对应 ``router.py`` 和 C# 的分支判断."""

    MAP = "map"
    BUILD = "build"
    COMBAT = "combat"
    # 选牌弹窗 (弃牌 / 消耗 / 升级) 由独立决策单元处理.
    # 它们会中断战斗, 但不属于战斗出牌决策,
    # 候选牌来自手牌, 而非奖励列表.
    SELECT = "select"


class Source(str, Enum):
    """决策来源, 仅写入日志, 游戏不会解析."""

    LLM = "llm"
    SEARCH = "search"
    HEURISTIC = "heuristic"
    SCRIPT = "script"
    NONE = "none"


@dataclass(frozen=True)
class DecisionRequest:
    kind: DecisionKind
    state: GameState
    scene: str = ""  # BUILD 的子界面: card_reward / shop / event / rest / rewards
    legal: tuple[Action, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        """可移植到 C# 的数据形状; ``state.raw`` 已是原始协议形状."""
        return {
            "kind": self.kind.value,
            "scene": self.scene,
            "state": self.state.raw,
            "legal": [a.to_payload() for a in self.legal],
        }


@dataclass(frozen=True)
class Decision:
    action: Optional[Action] = None
    reason: str = ""
    source: Source = Source.NONE
    tool: str = ""
    # 可序列化为 JSON 的日志详情. 对于 LLM 决策, 包含
    # 模型名, token 用量, 提示词和原始回复,
    # 便于回放时查看模型实际收到的内容.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_wait(self) -> bool:
        return self.action is None and self.source is Source.NONE

    def to_wire(self) -> dict[str, Any]:
        wire = {
            "action": self.action.to_payload() if self.action else None,
            "reason": self.reason,
            "source": self.source.value,
            "tool": self.tool,
        }
        if self.extra:
            wire["extra"] = self.extra
        return wire


WAIT = Decision(action=None, reason="等待状态稳定", source=Source.NONE, tool="none")


@dataclass(frozen=True)
class DecisionLog:
    """回放日志中的一条记录."""

    step: int
    screen: str
    request: Optional[dict[str, Any]]
    decision: dict[str, Any]
    elapsed_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
