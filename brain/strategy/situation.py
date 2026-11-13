"""模型局面输入：供地图和构筑决策整理最新状态、牌组报告、Boss 信息及删牌价格估算。

本模块保留现有统计与价格口径，不选择动作，也不将牌组统计作为完整战斗模拟结果。
"""

from __future__ import annotations

from dataclasses import asdict

from ..model import GameState
from .context import StrategyContext


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

# 本幕 Boss 的已知机制与选牌对策 (来自游戏内状态与社区 wiki, 用 id 前缀匹配).
BOSS_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("VANTOM", "墨影幻灵",
     "固有滑溜: 每次失去生命只掉 1 点, 单一高伤攻击会被浪费; 优先多段攻击(飞剑回旋镖/连续反弹/双重打击)"),
    ("CEREMONIAL_BEAST", "仪式兽",
     "血量降到阈值会被击晕并清零力量: 用多段或爆发在击晕窗口打出, 二阶段会被上昏眩(每回合只能出1张)"),
    ("WATERFALL_GIANT", "瀑布巨兽",
     "会回血且有'蒸汽喷发': 击杀后下回合仍要吃伤害, 不能靠拖延, 需要稳定持续输出"),
    ("THE_KIN", "同族",
     "召唤小怪: 优先群攻与小怪清理, 别让场面滚雪球"),
)


def boss_profile(state: GameState) -> dict:
    """当前幕 Boss 的已知机制, 用于针对性选牌."""
    map_raw = state.raw.get("map") or {}
    boss = map_raw.get("boss") or {}
    boss_id = str(boss.get("id") or "")
    name = str(boss.get("name") or "")
    for prefix, boss_name, note in BOSS_PROFILES:
        if boss_id.startswith(prefix) or (name and name == boss_name):
            return {"name": boss_name, "note": note}
    if name:
        return {"name": name, "note": "机制未收录, 只按可见意图判断"}
    return {}

# 两件套组合: (部件, 缺失的另一半, 说明). 手里有其中一件时优先补另一件.
COMBOS: tuple[tuple[str, str, str], ...] = (
    ("壁垒", "全身撞击", "壁垒积累格挡, 全身撞击把格挡变成伤害"),
    ("腐化", "黑暗之拥", "技能免费后靠消耗抽牌形成循环"),
    ("腐化", "无痛", "技能免费后每次消耗都给格挡"),
    ("恶魔形态", "飞剑回旋镖", "力量成长需要多段攻击放大"),
    ("破裂", "烙印", "烙印是最便宜的自伤触发"),
    ("残酷", "痛击", "易伤放大需要易伤来源"),
    ("主宰", "不可动摇", "主宰靠获得格挡输出, 不可动摇让格挡翻倍"),
)

# 对策卡: 针对特定威胁, 本幕拿 1~2 张即可, 不要提前囤.
COUNTER_CARDS: dict[str, str] = {
    "旋风斩": "多敌",
    "连续反弹": "多敌",
    "闪电霹雳": "多敌",
    "突破": "多敌",
    "燃烧契约": "状态牌",
    "坚毅": "状态牌",
    "战栗": "高伤单体",
    "挑衅": "高伤单体",
    "上勾拳": "高伤单体",
    "剑柄打击": "过牌",
    "耸肩无视": "过牌",
}


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
    # 两件套: 已有半件时, 另一半是高优先目标.
    present = {n.rstrip("+") for n in names}
    combos_ready = [note for a, b, note in COMBOS if a in present and b in present]
    combos_missing = []
    for a, b, note in COMBOS:
        # 两个方向都要提示: 有壁垒时缺全身撞击, 有全身撞击时缺壁垒.
        if a in present and b not in present:
            combos_missing.append(f"{b}（已有 {a}：{note}）")
        elif b in present and a not in present:
            combos_missing.append(f"{a}（已有 {b}：{note}）")
    counter_coverage: dict[str, list[str]] = {}
    for n in present:
        role = COUNTER_CARDS.get(n)
        if role:
            counter_coverage.setdefault(role, []).append(n)
    return {
        "size": len(deck),
        "strikes": names.count("打击"),
        "defends": names.count("防御"),
        "attack_cards": len(attacks),
        "block_cards": block_sources,
        "engine_cards": sorted({n for n in names if n.rstrip("+") in ENGINE_CARDS}),
        "payoff_cards": sorted({n for n in names if n.rstrip("+") in PAYOFF_CARDS}),
        "vulnerable_sources": sorted({n for n in names if n.rstrip("+") in VULNERABLE_SOURCES}),
        # 组合: ready 是两个部件都齐, missing 是只差一半(优先补).
        "combos_ready": combos_ready,
        "combos_missing": combos_missing,
        # 对策覆盖: 每种威胁有几张对策牌, 建议 1~2 张即可.
        "counter_coverage": {k: sorted(v) for k, v in counter_coverage.items()},
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
        # 本幕 Boss 的已知机制: 选牌要针对它 (例如滑溜 -> 多段攻击).
        "act_boss": boss_profile(state),
        "simulation_available": False,
    }
