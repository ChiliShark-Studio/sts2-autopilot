"""组装入口: 使用各组件构建连接真实游戏的 Agent.

这里集中引用所有具体实现. 替换战斗搜索为 MCTS, 或更换地图 LLM 时,
只需调整此处的组装代码.
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
                journal_dir: Optional[Path] = None,
                stop_after_run: bool = False) -> GameAgent:
    client = None if offline else LLMClient.from_env()
    journal = Journal.create(journal_dir) if journal_dir else None
    return GameAgent(
        Bridge(base),
        map_agent=MapAgent(MapRouterTool(client)),
        build_agent=BuildAgent(BuildFlowTool(client)),
        combat_agent=CombatAgent(CombatSearchTool()),
        select_agent=SelectAgent(SelectTool()),
        journal=journal,
        stop_after_run=stop_after_run,
    )
