"""地图 Agent: 将路线决策交给地图工具."""

from __future__ import annotations

from ..contracts import Decision, DecisionRequest
from ..ports import ToolPort


class MapAgent:
    kind = "map"

    def __init__(self, tool: ToolPort) -> None:
        self.tool = tool

    def decide(self, request: DecisionRequest) -> Decision:
        return self.tool.decide(request)
