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
        return """铁甲战士（A10 环境）构筑原则：**先定主线，再按主线选牌**。

主线优先级（社区评级，来源：铁甲战士构筑指南 / A10 通关录像）：
1. 力量成长（S）：恶魔形态（每回合 +3 力量）、燃烧、烙印（0 费 +1 力量）提供力量，
   收益牌用旋风斩、飞剑回旋镖、重磅打击、连续反弹等多段或高倍率攻击。
   弱点启动慢：前两回合要留格挡。
2. 消耗引擎（S-，配壁垒 S+）：腐化 + 黑暗之拥 + 无痛，牌组目标 15~20 张；
   终局用契约终章、灰烬打击这类按消耗堆成长的牌。
3. 格挡壁垒（A+）：壁垒 + 全身撞击 + 主宰 + 不可动摇。
   全身撞击单独拿没有价值，必须先有壁垒或稳定高格挡来源。
4. 易伤放大层（A-）：痛击/战栗/上勾拳 铺易伤，欺凌/残忍 收伤害。
   只作放大层，不做主线。
A10 不要走纯自伤/破裂流（血量波动大，进阶 10 以上不稳）。

牌组构筑规则：
- 每张牌都要回答"服务哪条主线、解决当前什么问题"；答不上来就跳过。
- 开局前三层选定一条主线并写进 build_plan；之后选牌优先补主线的组件缺口。
  引擎件（力量来源/腐化/壁垒）优先于收益件；没有引擎时先拿引擎。
- 需要两件套才成立的牌（例如全身撞击需要壁垒、欺凌需要易伤来源）缺前置时不拿。
- 不要混搭两条主线的半成品；宁可一条主线成型，也不要四不像。
- 删牌优先级：先删打击，再删防御（基础牌只稀释抽到关键牌的概率）。
- 升级优先级：先升引擎与斩杀牌，最后才升防御。

第一幕以活着走下为主：
- 前五层可以抓高效过渡牌（单卡数值划算、立刻能用），之后不再抓填充牌；
- 至少一张群体或多段伤害、四张以上格挡来源、一到两张过牌。

经济：金币要变成牌组质量（删牌 > 关键牌 > 遗物），不要长期闲置；
买不起删牌或关键牌时优先攒钱，不要因为"买得起"就买填充牌。
进商店时牌组里还有基础打击或基础防御，删牌优先于买新牌。
最大生命是不可再生资源，遇到以最大生命为代价的事件要格外保守。

篝火默认锻造：升级是整局永久收益，回血只解决眼前。只有血量低于三成、下一节点就是 Boss 且血不够、
或牌组已没有值得升级的牌时才休息；回血次数不要超过本幕篝火总数的一半。
升级先给引擎与斩杀牌（增加段数、降低费用、显著提高数值），不要升级基础打击或防御。"""
    return f"当前角色：{character or '未知'}。尚无该角色专用攻略，只按状态中的实际效果分析配合，不套用铁甲战士策略。"


# 铁甲战士主线组件（按中文牌名, 桥接的牌组列表只给名字）.
ENGINE_CARDS = {
    # 力量来源
    "恶魔形态", "燃烧", "烙印", "撕裂",
    # 消耗引擎
    "腐化", "黑暗之拥", "无痛",
    # 壁垒
    "壁垒", "主宰", "不可动摇",
    # 易伤放大
    "残忍", "破裂",
}
PAYOFF_CARDS = {
    "旋风斩", "飞剑回旋镖", "重磅打击", "连续反弹", "双重打击",
    "契约终章", "灰烬打击", "全身撞击", "欺凌", "踩踏",
}
VULNERABLE_SOURCES = {"痛击", "战栗", "上勾拳"}


def deck_report(state: GameState) -> dict:
    """牌组体检: 把"还缺什么"算成模型能直接读的数字.

    只做结构统计, 不猜胜率. 每回合预期输出用
    ``攻击牌伤害总和 × 5 ÷ 牌组张数`` 估算 (未计力量与易伤).
    """
    deck = list(state.player.deck)
    if not deck:
        return {}
    names = [c.name for c in deck]
    attacks = [c for c in deck if c.damage > 0]
    damage_total = sum(c.damage * max(1, c.hits) for c in attacks)
    block_sources = sum(1 for c in deck if c.block > 0 or c.plated > 0)
    missing: list[str] = []
    if not any(n.rstrip("+") in ENGINE_CARDS for n in names):
        missing.append("引擎")
    if not attacks:
        missing.append("攻击牌")
    if not block_sources:
        missing.append("格挡来源")
    # 两件套的缺件提示: 这些牌单独拿到几乎没有价值.
    if "全身撞击" in names and "壁垒" not in names:
        missing.append("壁垒(全身撞击的前置)")
    if any(n.rstrip("+") in {"欺凌", "残忍"} for n in names) and not any(
            n.rstrip("+") in VULNERABLE_SOURCES for n in names):
        missing.append("易伤来源(欺凌/残忍的前置)")
    return {
        "size": len(deck),
        "strikes": names.count("打击"),
        "defends": names.count("防御"),
        "attack_cards": len(attacks),
        "block_cards": block_sources,
        "engine_cards": sorted({n for n in names if n.rstrip("+") in ENGINE_CARDS}),
        "payoff_cards": sorted({n for n in names if n.rstrip("+") in PAYOFF_CARDS}),
        "vulnerable_sources": sorted({n for n in names if n.rstrip("+") in VULNERABLE_SOURCES}),
        # 估算: 满牌组循环一次的总伤害 × 每回合 5 抽 ÷ 牌组张数.
        "damage_per_turn": round(damage_total * 5 / len(deck), 1),
        "missing": missing,
    }
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
        # 牌组体检: 引擎/收益件/每回合预期输出, 供模型判断"还缺什么".
        "deck_report": deck_report(state),
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
