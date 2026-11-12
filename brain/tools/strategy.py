"""选路与构筑共用的局面输入、角色提示和合法候选选择。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..actions import Action, ActionError
from ..contracts import Decision, Source
from ..effects import humanize
from ..model import GameState


# 模型回复无效时的重试次数; 用尽后停下等待排查, 不再退回启发式.
LLM_ATTEMPTS = 3


def no_decision(tool: str, reason: str) -> Decision:
    """模型没有给出可用决策: 停下, 而不是猜一个动作."""
    return Decision(action=None, reason=reason, source=Source.NONE, tool=tool)


@dataclass
class StrategyContext:
    """仅保留本局的构筑判断与路线计划；实际资源始终读取最新状态。"""

    plan: str = ""
    # 开局规划出的路线, 每项是 (col, row); 为空表示需要在当前节点重新规划.
    route: tuple[tuple[int, int], ...] = ()
    route_note: str = ""
    # 本局已购买的删牌服务次数: 删牌价格随次数上涨.
    removals: int = 0

    def reset(self) -> None:
        self.plan = ""
        self.route = ()
        self.route_note = ""
        self.removals = 0


def character_prompt(state: GameState) -> str:
    character = state.player.character
    if character.upper() == "IRONCLAD" or character == "铁甲战士":
        return """铁甲战士构筑原则：先保证当前阶段的输出和防御，再根据实际组件考虑力量成长、消耗配合等方向。
不要预先锁定流派；只有当前卡牌和遗物的效果支持时，才判断配合成立。
同时检查费用、抽牌、启动速度和关键牌密度；删牌与升级应提高整套卡组的运转能力，不只比较单牌数值。

第一幕以活着走下为主：可以抓高效过渡牌（单卡伤害或格挡与费用划算、能立刻用上的过牌），不必等完美配合；
但过渡牌只在前五层左右值得抓，第一幕后段与第二幕起不再买或保留填充牌
（例如 1 费 5 格挡加 5 伤害这类拆分牌，二幕前本该删掉）。
需要两件套才成立的能力牌，缺触发源时先不拿。
第一幕优先补齐：至少一张群体或多段伤害、四张以上格挡来源、一到两张过牌。
第一幕结束时牌组控制在 12 到 15 张：超过 15 张后只接受核心牌（成长、群攻、过牌、关键格挡），其余跳过。
牌组达到十几张后提高取牌门槛：只接受服务当前方向的牌，其余跳过。
基础打击偏多时，商店优先删打击；金币要变成牌组质量（删牌、关键牌、遗物），不要长期闲置。
钱不够删牌或买关键牌时优先攒钱，不要因为"买得起"就买填充牌。
进商店时如果牌组里还有基础打击或基础防御，删牌优先于买新牌。
最大生命是不可再生资源，遇到以最大生命为代价的事件要格外保守。

篝火默认锻造：升级是整局永久收益，回血只解决眼前。只有血量低于三成、下一节点就是 Boss 且血不够、
或牌组已没有值得升级的牌时才休息；回血次数不要超过本幕篝火总数的一半。
升级优先"升级后质变"的牌（增加段数、降低费用、显著提高易伤层数或格挡数值），
不要升级基础打击或防御；不拿随机目标、与现有牌功能重叠、或需要专门配合才成立的牌。"""
    return f"当前角色：{character or '未知'}。尚无该角色专用攻略，只按状态中的实际效果分析配合，不套用铁甲战士策略。"


def removal_price(ascension: int, removals: int) -> int:
    """商店删牌服务的下一次价格.

    规则 (Spire Codex 商店机制): A0-5 为 75 + 25n; A6 起涨价,
    起始 100、每次 +50. n 是已经买过的删牌次数.
    """
    base, step = (100, 50) if ascension >= 6 else (75, 25)
    return base + step * max(0, removals)


def situation(state: GameState, context: StrategyContext) -> dict:
    """保留桥接完整效果，避免领域模型中仅存名称或缺省值造成信息丢失。"""
    player = state.raw.get("player")
    return {
        "run": state.raw.get("run") or asdict(state.run),
        "player": player if isinstance(player, dict) else asdict(state.player),
        "map": state.raw.get("map"),
        "scene": state.raw.get(state.screen.value),
        # 商店相关: 删牌价格随已删次数上涨, 规划路线时要用估算价而不是固定 75.
        "shop": {
            "removals_bought": context.removals,
            "next_removal_price": removal_price(state.run.ascension, context.removals),
        },
        "previous_build_plan": context.plan,
        "simulation_available": False,
    }


def combat_selection(state: GameState) -> bool:
    # 桥接的网格覆盖层可能没有 battle；hand 仅在实际战斗中提供。
    return state.battle is not None or "hand" in (state.raw.get("player") or {})


def choose(client, state: GameState, context: StrategyContext, *, task: str,
           candidates: list[tuple[int, Action, dict]], tool: str,
           allow_combat: bool = False, attempts: int = 1) -> Decision | None:
    """LLM 只能返回现有候选编号，不直接执行它生成的任意动作。

    ``allow_combat`` 只用于"战斗内无法模拟的药水"这类必须由模型判断的情况；
    常规出牌决策仍然不得调用模型。
    ``attempts`` 用于模型回复无效时重试；用尽后返回 None，由调用方决定停下。
    """
    if client is None or not candidates or (combat_selection(state) and not allow_combat):
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
    body = humanize(json.dumps(payload, ensure_ascii=False))
    actions = {index: action for index, action, _ in candidates}
    for _ in range(max(1, attempts)):
        reply = client.json_chat(system, body)
        if not isinstance(reply, dict) or type(reply.get("choice")) is not int:
            continue
        if reply["choice"] not in actions:
            continue
        action = actions[reply["choice"]]
        try:
            action.check(state)
        except ActionError:
            continue
        plan = reply.get("build_plan")
        if isinstance(plan, str) and plan.strip():
            context.plan = plan.strip()[:1000]
        return Decision(
            action=action,
            reason=str(reply.get("reason") or "采用模型选择"),
            source=Source.LLM, tool=tool,
            extra={"llm": dict(client.last_meta or {}), "build_plan": context.plan},
        )
    return None
