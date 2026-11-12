"""战斗 Agent: 负责战斗决策, 将搜索交给工具实现."""

from __future__ import annotations

from ..contracts import Decision, DecisionRequest
from ..ports import ToolPort


class CombatAgent:
    kind = "combat"

    def __init__(self, tool: ToolPort) -> None:
        self.tool = tool

    def decide(self, request: DecisionRequest) -> Decision:
        return self.tool.decide(request)
