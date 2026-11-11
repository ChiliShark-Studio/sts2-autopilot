"""Combat agent: owns combat decisions, delegates the search to a tool."""

from __future__ import annotations

from ..contracts import Decision, DecisionRequest
from ..ports import ToolPort


class CombatAgent:
    kind = "combat"

    def __init__(self, tool: ToolPort) -> None:
        self.tool = tool

    def decide(self, request: DecisionRequest) -> Decision:
        return self.tool.decide(request)
