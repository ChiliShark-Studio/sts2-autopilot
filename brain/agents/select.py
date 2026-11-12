"""选牌 Agent: 负责弃牌 / 消耗 / 升级等选牌弹窗的决策."""

from __future__ import annotations

from ..contracts import Decision, DecisionRequest
from ..ports import ToolPort


class SelectAgent:
    kind = "select"

    def __init__(self, tool: ToolPort) -> None:
        self.tool = tool

    def decide(self, request: DecisionRequest) -> Decision:
        return self.tool.decide(request)
