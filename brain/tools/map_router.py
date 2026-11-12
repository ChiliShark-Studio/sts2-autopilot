"""地图工具: 开局规划整条路线, 到分叉点再重新规划; 启发式作为兜底.

逐步询问模型有两个问题: 单选项节点也要调用一次, 而且每次只看下一跳,
容易为眼前收益走偏. 因此这里把路线当成一次规划:

  * 有可用计划时只执行计划中的下一跳, 不调用模型.
  * 唯一可选节点直接前进, 不调用模型.
  * 计划用完 (到达分叉点) 或计划失效 (目标不可达) 时才重新规划一次.

模型返回的坐标序列必须与地图节点和连边一致, 否则退回启发式,
避免把不存在的节点传给游戏.
"""

from __future__ import annotations

import json
from typing import Optional

from ..actions import ChooseMapNode
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..llm import LLMClient
from ..model import GameState
from .strategy import LLM_ATTEMPTS, StrategyContext, character_prompt, no_decision, situation

NodeKey = tuple[int, int]

# 一次规划的路线长度上限, 防止模型返回异常长的序列.
MAX_ROUTE = 30
# 计划中的下一跳是精英时, 用模拟器按本幕所有精英各跑几局再判断.
# 地图上不显示是哪一只精英, 所以只看最坏情况.
ELITE_SIM_RUNS = 2
# 本幕没有可模拟的精英时, 退回按血量比例征询.
ELITE_RECHECK_HP = 0.5


def build_prompt(state: GameState) -> str:
    return json.dumps(situation(state, StrategyContext()), ensure_ascii=False)


def _describe_risk(risk) -> str:  # noqa: ANN001 - sim.risk.EliteAssessment, 延迟导入避免循环
    """把逐精英的模拟结果写成模型能读的一句话."""
    parts = [f"{r.name} 胜率{r.win_rate:.0%}/掉血{r.avg_damage:.0f}-{r.max_damage}"
             for r in risk.risks]
    return (", ".join(parts)
            + f" → 最坏 胜率{risk.worst_win_rate:.0%}、掉血{risk.worst_damage} 血")


def _map_raw(state: GameState) -> dict:
    raw = state.raw.get("map")
    return raw if isinstance(raw, dict) else {}


def _options(map_raw: dict) -> list[dict]:
    return [o for o in map_raw.get("next_options") or [] if isinstance(o, dict)]


def _key(node: dict) -> Optional[NodeKey]:
    try:
        return int(node["col"]), int(node["row"])
    except (KeyError, TypeError, ValueError):
        return None


def _option_keys(options: list[dict]) -> list[NodeKey]:
    keys = []
    for option in options:
        key = _key(option)
        if key is not None:
            keys.append(key)
    return keys


def _nodes(map_raw: dict) -> dict[NodeKey, str]:
    """按坐标索引节点类型, 用于校验计划中的节点确实存在."""
    out: dict[NodeKey, str] = {}
    for node in map_raw.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = _key(node)
        if key is not None:
            out[key] = str(node.get("type") or "")
    return out


def _children(map_raw: dict) -> dict[NodeKey, set[NodeKey]]:
    out: dict[NodeKey, set[NodeKey]] = {}
    for node in map_raw.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = _key(node)
        if key is None:
            continue
        links = set()
        for child in node.get("children") or []:
            if isinstance(child, (list, tuple)) and len(child) >= 2:
                try:
                    links.add((int(child[0]), int(child[1])))
                except (TypeError, ValueError):
                    continue
        out[key] = links
    return out


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


def validate_route(raw_route, map_raw: dict, options: list[dict]) -> Optional[list[NodeKey]]:
    """校验模型给出的路线, 返回从当前节点开始的最长合法前缀.

    模型可能只在中间某一跳写错 (例如漏掉一个节点的连边), 这种情况下
    保留前面已验证的部分仍然有价值; 只有第一跳非法时才放弃整条计划.
    """
    if not isinstance(raw_route, list) or not raw_route:
        return None
    nodes = _nodes(map_raw)
    children = _children(map_raw)
    start_options = set(_option_keys(options))

    route: list[NodeKey] = []
    for item in raw_route[:MAX_ROUTE]:
        key = _key(item) if isinstance(item, dict) else None
        if key is None and isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                key = (int(item[0]), int(item[1]))
            except (TypeError, ValueError):
                break
        if key is None or key not in nodes:
            break
        if not route:
            if key not in start_options:
                return None
        elif key not in children.get(route[-1], set()):
            break
        route.append(key)
    return route or None


class MapRouterTool:
    name = "map_router"

    def __init__(self, client: Optional[LLMClient] = None,
                 context: Optional[StrategyContext] = None) -> None:
        self.client = client
        self.context = context if context is not None else StrategyContext()
        # 上一次发过动作的 (位置, 选中节点). 桥接在移动生效前可能再返回一帧旧状态,
        # 那一帧的候选里没有刚选过的节点, 不能当成新决策处理.
        self._awaiting_move: Optional[tuple[NodeKey, NodeKey]] = None

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        map_raw = _map_raw(state)
        options = _options(map_raw)
        if not options:
            # 地图正在切换, 选项会在一两帧后重新出现.
            return WAIT

        position = _key(map_raw.get("current_position") or {})
        if self._awaiting_move is not None:
            if position == self._awaiting_move[0]:
                # 位置没变: 上一帧的动作还没生效, 等待而不是再次选择.
                return WAIT
            self._awaiting_move = None

        keys = _option_keys(options)
        planned = self.context.route[0] if self.context.route else None
        if planned is not None and planned not in keys:
            # 计划中的下一跳已经不在候选里: 地图状态变化, 计划作废.
            self.context.route = ()
            self.context.route_note = "计划节点不可达"

        if len(options) == 1:
            if planned == keys[0]:
                self.context.route = self.context.route[1:]
            self._awaiting_move = (position, keys[0])
            return self._choose(options[0], f"唯一可选节点：{options[0].get('type')}",
                                Source.SCRIPT)

        if planned is not None and planned in keys:
            node_type = _nodes(map_raw).get(planned, "")
            risk = self._elite_risk(state) if node_type == "Elite" else None
            if risk is None:
                # 本幕没有可模拟的精英: 退回按血量比例征询.
                needs_recheck = (node_type == "Elite"
                                 and state.player.hp_ratio < ELITE_RECHECK_HP)
                replan_reason = (
                    f"计划中的下一节点是精英, 但当前血量 {state.player.hp}/{state.player.max_hp}, "
                    f"重新评估是否仍要走精英" if needs_recheck else "")
            else:
                needs_recheck = not risk.affordable(state.player.hp)
                replan_reason = (
                    f"计划中的下一节点是精英。模拟结果(按本幕所有精英, 取最坏): {_describe_risk(risk)}。"
                    f"当前血量 {state.player.hp}/{state.player.max_hp}. 重新评估路线, 决定绕开还是接受。"
                    if needs_recheck else "")
            if not needs_recheck:
                index = keys.index(planned)
                self.context.route = self.context.route[1:]
                note = self.context.route_note or "开局计划"
                self._awaiting_move = (position, planned)
                return self._choose(options[index], f"按路线计划前往 {planned}：{note}",
                                    Source.SCRIPT)
            # 精英不再是既定路线: 把选择权交回模型.
            self.context.route_note = replan_reason
        else:
            replan_reason = ""

        if self.client is None:
            return self._fallback(state, "离线运行，使用启发式")

        planned_route = self._plan(state, map_raw, options, replan_reason)
        if planned_route is None:
            # 有模型时不退回启发式: 连续失败就停下, 让人看到问题.
            return no_decision(self.name, f"模型连续 {LLM_ATTEMPTS} 次未给出合法路线")

        route, reason = planned_route
        route_before = [list(k) for k in self.context.route]
        self.context.route = tuple(route)
        self.context.route_note = reason
        first = route[0]
        self.context.route = tuple(route[1:])
        index = keys.index(first)
        self._awaiting_move = (position, first)
        prefix = "重新规划" if replan_reason else "开局规划"
        return Decision(
            action=ChooseMapNode(options[index].get("index", 0)),
            reason=f"{prefix}前往 {first}：{reason}",
            source=Source.LLM,
            tool=self.name,
            extra={"llm": dict(self.client.last_meta or {}), "route": [list(k) for k in route],
                   "route_reason": reason, "route_before": route_before},
        )

    def _choose(self, option: dict, reason: str, source: Source) -> Decision:
        return Decision(action=ChooseMapNode(option.get("index", 0)), reason=reason,
                        source=source, tool=self.name)

    def _fallback(self, state: GameState, note: str) -> Decision:
        index, reason = fallback(state)
        map_raw = _map_raw(state)
        keys = _option_keys(_options(map_raw))
        if 0 <= index < len(keys):
            self._awaiting_move = (_key(map_raw.get("current_position") or {}), keys[index])
        self.context.route = ()
        extra = {}
        if self.client is not None and self.client.last_meta:
            # 记录最后一次模型回复, 便于排查计划为什么被判定为无效.
            extra["llm"] = dict(self.client.last_meta)
        return Decision(action=ChooseMapNode(index), reason=f"{note}；{reason}",
                        source=Source.HEURISTIC, tool=self.name, extra=extra)

    def _elite_risk(self, state: GameState):
        """用当前牌组与血量, 按本幕可能出现的精英各跑几局.

        地图上不显示具体是哪一只精英, 所以只能评估最坏情况;
        本幕没有可模拟的精英时返回 None, 调用方退回血量阈值.
        """
        # 延迟导入: brain.sim 会经由 combat_search 反向导入本模块.
        from ..sim import scenarios
        from ..sim.engine import deck_from_cards
        from ..sim.risk import assess

        act = state.run.act
        if act not in scenarios.ELITES_BY_ACT:
            return None
        deck = deck_from_cards(list(state.player.deck))
        if not deck:
            return None
        return assess(deck, state.player.hp, state.player.max_hp, act,
                      runs=ELITE_SIM_RUNS)

    def _plan(self, state: GameState, map_raw: dict, options: list[dict],
              replan_reason: str = "") -> Optional[tuple[list[NodeKey], str]]:
        """让模型一次性规划路线, 返回校验过的坐标序列与理由."""
        assert self.client is not None
        system = f"""你是《杀戮尖塔2》的选路助手。{character_prompt(state)}
请根据完整可见地图, 从当前候选节点出发规划接下来的路线:
- 尽量一次规划到本幕 Boss; 若某个分叉确实需要到时候的血量或资源才能判断, 可以只规划到该分叉点为止.
- route 是按行列坐标依次相连的节点序列, 第一个必须是当前候选之一, 之后每一步都必须与上一步直接相连.
- 路线要服务当前构筑、血量、金币和构筑需求; 只分析可见地图, 不假定未知节点的具体敌人.
- 篝火是升级点而非回血点: 优先经过篝火较多的路径, 但第一幕前半段先打几场弱怪补牌,
  并避免连续多场战斗没有篝火补给; 不要为了回血反复绕路.
- 商店的删牌价格会随已删次数上涨, 用 situation.shop.next_removal_price 判断, 不要按固定 75 金估算;
  金币接近该价格且牌组仍有基础打击/防御时, 商店是高价值节点.
- 若 replan_reason 非空, 说明原计划已被重新评估: 请结合当前血量与资源重新决定, 可以坚持原路线, 也可以改道.
只输出 JSON：{{"route": [[col, row], ...], "stop": "boss" 或 "fork" 或 "now", "reason": "<简短中文依据>"}}"""
        payload = {
            "situation": situation(state, self.context),
            "options": [{"index": o.get("index"), "col": o.get("col"), "row": o.get("row"),
                         "type": o.get("type")} for o in options],
            "previous_route": [list(k) for k in self.context.route],
            "previous_route_note": self.context.route_note,
            "replan_reason": replan_reason,
        }
        reply = None
        for _ in range(max(1, LLM_ATTEMPTS)):
            reply = self.client.json_chat(system, json.dumps(payload, ensure_ascii=False))
            if isinstance(reply, dict):
                route = validate_route(reply.get("route"), map_raw, options)
                if route is not None:
                    reason = str(reply.get("reason") or "按模型规划").strip()[:200]
                    return route, reason
        return None
