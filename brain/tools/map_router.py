"""地图工具: 使用 LLM 排序路线, 启发式作为兜底.

路线价值同时依赖卡组强度, 血量, 遗物, 金币和后续两层, 固定评分难以完整表达.
模型输出必须经过合法选项校验, 防止不存在的索引传入游戏.
"""

from __future__ import annotations

from typing import Optional

from ..actions import ChooseMapNode
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..llm import LLMClient
from ..model import GameState
from .strategy import StrategyContext, choose, situation


def build_prompt(state: GameState) -> str:
    import json

    return json.dumps(situation(state, StrategyContext()), ensure_ascii=False)


def fallback(state: GameState) -> tuple[int, str]:
    p, map_state = state.player, state.map
    assert map_state and map_state.options
    hp = p.hp_ratio
    base = {"Elite": 0.8, "Monster": 1.0, "Unknown": 1.1, "Shop": 1.2,
            "RestSite": 1.35, "Treasure": 1.45}
    scored = []
    for opt in map_state.options:
        score = base.get(opt.type, 1.0)
        if hp < 0.5:
            if opt.type == "RestSite":
                score += 1.2
            elif opt.type == "Elite":
                score -= 2.0
            elif opt.type == "Monster":
                score -= 0.4
        elif hp > 0.75 and state.run.act <= 2 and opt.type == "Elite":
            score += 0.8
        if opt.type == "Shop" and p.gold < 60:
            score -= 0.9
        scored.append((score, opt))
    scored.sort(key=lambda item: (-item[0], item[1].index))
    score, best = scored[0]
    return best.index, f"血量{p.hp}/{p.max_hp}、金币{p.gold}，启发式选 {best.type}（{score:.1f}）"


class MapRouterTool:
    name = "map_router"

    def __init__(self, client: Optional[LLMClient] = None,
                 context: Optional[StrategyContext] = None) -> None:
        self.client = client
        self.context = context if context is not None else StrategyContext()

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        if state.map is None or not state.map.options:
            # 地图正在切换, 选项会在一两帧后重新出现.
            return WAIT

        decision = choose(
            self.client, state, self.context,
            task="选择下一个地图节点，结合后续可见路线比较恢复、成长与构筑需求。",
            candidates=[(o.index, ChooseMapNode(o.index),
                         {"type": o.type, "leads_to": list(o.leads_to)}) for o in state.map.options],
            tool=self.name,
        )
        if decision is not None:
            return decision

        index, reason = fallback(state)
        return Decision(action=ChooseMapNode(index), reason=reason, source=Source.HEURISTIC, tool=self.name)
