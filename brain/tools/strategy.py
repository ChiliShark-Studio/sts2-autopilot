"""选路与构筑共用的局面输入、角色提示和合法候选选择。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..actions import Action, ActionError
from ..contracts import Decision, Source
from ..model import GameState


@dataclass
class StrategyContext:
    """仅保留本局的构筑判断；实际资源始终读取最新状态。"""

    plan: str = ""

    def reset(self) -> None:
        self.plan = ""


def character_prompt(state: GameState) -> str:
    character = state.player.character
    if character.upper() == "IRONCLAD" or character == "铁甲战士":
        return """铁甲战士构筑原则：先保证当前阶段的输出和防御，再根据实际组件考虑力量成长、消耗配合等方向。
不要预先锁定流派；只有当前卡牌和遗物的效果支持时，才判断配合成立。
同时检查费用、抽牌、启动速度和关键牌密度；删牌与升级应提高整套卡组的运转能力，不只比较单牌数值。"""
    return f"当前角色：{character or '未知'}。尚无该角色专用攻略，只按状态中的实际效果分析配合，不套用铁甲战士策略。"


def situation(state: GameState, context: StrategyContext) -> dict:
    """保留桥接完整效果，避免领域模型中仅存名称或缺省值造成信息丢失。"""
    player = state.raw.get("player")
    return {
        "run": state.raw.get("run") or asdict(state.run),
        "player": player if isinstance(player, dict) else asdict(state.player),
        "map": state.raw.get("map"),
        "scene": state.raw.get(state.screen.value),
        "previous_build_plan": context.plan,
        "simulation_available": False,
    }


def combat_selection(state: GameState) -> bool:
    # 桥接的网格覆盖层可能没有 battle；hand 仅在实际战斗中提供。
    return state.battle is not None or "hand" in (state.raw.get("player") or {})


def choose(client, state: GameState, context: StrategyContext, *, task: str,
           candidates: list[tuple[int, Action, dict]], tool: str) -> Decision | None:
    """LLM 只能返回现有候选编号，不直接执行它生成的任意动作。"""
    if client is None or not candidates or combat_selection(state):
        return None
    system = f"""你是《杀戮尖塔2》的选路与构筑助手。{task}
{character_prompt(state)}
根据最新局面重新检查已有组件、主要短板和下一步需求，之前的构筑计划仅供参考。
买卡、买遗物、删牌和留钱需要比较整体收益；药水需要考虑槽位与后续战斗。
路线要服务于构筑、恢复和资源需求，只分析可见地图，不假定未知节点的具体敌人。
效果以输入的当前游戏描述为准；尚未接入模拟器或经版本核实的攻略，不编造规则、精确胜率或已模拟的结论。
只能选择 candidates 中的 choice 整数，每次仅执行一个动作，随后重新读取状态。
只输出 JSON：{{"choice": <候选整数>, "reason": "<简短中文依据>", "build_plan": "<已有组件、主要短板、下一步需求，300字以内>"}}"""
    payload = {
        "situation": situation(state, context),
        "task": task,
        "candidates": [
            {"choice": index, "action": action.to_payload(), "details": details}
            for index, action, details in candidates
        ],
    }
    reply = client.json_chat(system, json.dumps(payload, ensure_ascii=False))
    if not isinstance(reply, dict) or type(reply.get("choice")) is not int:
        return None
    actions = {index: action for index, action, _ in candidates}
    if reply["choice"] not in actions:
        return None
    action = actions[reply["choice"]]
    try:
        action.check(state)
    except ActionError:
        return None
    plan = reply.get("build_plan")
    if isinstance(plan, str) and plan.strip():
        context.plan = plan.strip()[:1000]
    return Decision(
        action=action,
        reason=str(reply.get("reason") or "采用模型选择"),
        source=Source.LLM, tool=tool,
        extra={"llm": dict(client.last_meta or {}), "build_plan": context.plan},
    )
