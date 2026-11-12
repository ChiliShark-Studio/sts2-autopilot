"""构筑 Agent: 负责卡组构筑, 奖励和非战斗界面."""

from __future__ import annotations

from ..contracts import Decision, DecisionRequest
from ..ports import ToolPort


class BuildAgent:
    kind = "build"

    def __init__(self, tool: ToolPort) -> None:
        self.tool = tool

    def decide(self, request: DecisionRequest) -> Decision:
        return self.tool.decide(request)
