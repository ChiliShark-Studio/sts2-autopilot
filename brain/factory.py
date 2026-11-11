"""Composition root: build a live agent out of the pieces.

This is the one place that knows about all concrete implementations.  Swapping
the combat search for MCTS, or the map LLM for a different model, is a change
here and nowhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agents.build import BuildAgent
from .agents.combat import CombatAgent
from .agents.game import GameAgent
from .agents.map import MapAgent
from .agents.select import SelectAgent
from .llm import LLMClient
from .replay import Journal
from .tools.build_flow import BuildFlowTool
from .tools.combat_search import CombatSearchTool
from .tools.map_router import MapRouterTool
from .tools.select_tool import SelectTool
from .transport import Bridge

DEFAULT_BASE = "http://127.0.0.1:15526"


def build_agent(base: str = DEFAULT_BASE, *, offline: bool = False,
                journal_dir: Optional[Path] = None) -> GameAgent:
    client = None if offline else LLMClient.from_env()
    journal = Journal.create(journal_dir) if journal_dir else None
    return GameAgent(
        Bridge(base),
        map_agent=MapAgent(MapRouterTool(client)),
        build_agent=BuildAgent(BuildFlowTool()),
        combat_agent=CombatAgent(CombatSearchTool()),
        select_agent=SelectAgent(SelectTool()),
        journal=journal,
    )
