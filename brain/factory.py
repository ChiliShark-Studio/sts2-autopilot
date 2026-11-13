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
from .combat.enemy_rules import RuleBook
from .llm import LLMClient
from .replay import Journal
from .build.flow import BuildFlowTool
from .build.select import BuildSelect
from .combat.search import CombatSearchTool
from .combat.select import CombatSelect
from .combat.tempo_advisor import PowerAdvisor, TempoAdvisor
from .map.router import MapRouterTool
from .combat.potion_advisor import PotionAdvisor
from .strategy.context import StrategyContext
from .transport import Bridge

DEFAULT_BASE = "http://127.0.0.1:15526"


def build_agent(base: str = DEFAULT_BASE, *, offline: bool = False,
                journal_dir: Optional[Path] = None,
                stop_after_run: bool = False) -> GameAgent:
    client = None if offline else LLMClient.from_env()
    journal = Journal.create(journal_dir) if journal_dir else None
    context = StrategyContext()
    return GameAgent(
        Bridge(base),
        map_agent=MapAgent(MapRouterTool(client, context)),
        build_agent=BuildAgent(BuildFlowTool(client, context)),
        combat_agent=CombatAgent(CombatSearchTool(advisor=PotionAdvisor(client, context),
                                                  rules=RuleBook.with_defaults(),
                                                  tempo=TempoAdvisor(client),
                                                  power=PowerAdvisor(client))),
        select_agent=SelectAgent(BuildSelect(client, context), CombatSelect()),
        strategy_context=context,
        journal=journal,
        stop_after_run=stop_after_run,
    )
