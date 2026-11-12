"""地图工具: 使用 LLM 排序路线, 启发式作为兜底.

路线价值同时依赖卡组强度, 血量, 遗物, 金币和后续两层, 固定评分难以完整表达.
模型输出必须经过合法选项校验, 防止不存在的索引传入游戏.
"""

from __future__ import annotations

import json
from typing import Optional

from ..actions import ChooseMapNode
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..llm import LLMClient
from ..model import GameState

SYSTEM_PROMPT = """你是《杀戮尖塔2》的路线规划助手。你只负责选择下一个地图节点，不负责战斗中的出牌。

判断原则：
- 生命值是资源。血量低（<50%）时优先休息点/商店，避开精英。
- 精英给遗物，但只在血量健康（>70%）且卡组有把握时才打。
- 商店需要至少 60 金币才有意义。
- 卡组质量比数量重要；路线要服务于卡组。
- 未知节点风险中等，通常优于精英。

只输出 JSON：
{"choice": <候选里的 index 整数>, "reason": "<不超过40字的中文理由>"}
"""


def build_prompt(state: GameState) -> str:
    p, run, map_state = state.player, state.run, state.map
    lines = [
        "当前局面：",
        json.dumps({
            "act": run.act,
            "floor": run.floor,
            "ascension": run.ascension,
            "hp": p.hp,
            "max_hp": p.max_hp,
            "hp_ratio": round(p.hp_ratio, 2),
            "gold": p.gold,
            "character": p.character,
            "relics": list(p.relics),
            "potions": [p.name for p in p.potions],
            "boss": map_state.boss if map_state else "",
        }, ensure_ascii=False),
        "",
        f"卡组（{len(p.deck)} 张）：" + "、".join(c.name for c in p.deck[:40]),
        "",
        "可选节点：",
    ]
    for opt in (map_state.options if map_state else ()):
        ahead = "、".join(opt.leads_to) if opt.leads_to else "无"
        lines.append(f"- index={opt.index} 类型={opt.type} 之后可到={ahead}")
    lines.append("")
    lines.append('只输出 JSON：{"choice": <index>, "reason": "..."}')
    return "\n".join(lines)


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

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        if state.map is None or not state.map.options:
            # 地图正在切换, 选项会在一两帧后重新出现.
            return WAIT

        valid = {o.index for o in state.map.options}
        if self.client is not None:
            reply = self.client.json_chat(SYSTEM_PROMPT, build_prompt(state))
            if reply is not None:
                try:
                    choice = int(reply.get("choice"))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    choice = -1
                if choice in valid:
                    return Decision(
                        action=ChooseMapNode(choice),
                        reason=str(reply.get("reason") or "模型未给出理由"),
                        source=Source.LLM,
                        tool=self.name,
                        extra={"llm": self.client.last_meta},
                    )

        index, reason = fallback(state)
        return Decision(action=ChooseMapNode(index), reason=reason, source=Source.HEURISTIC, tool=self.name)
