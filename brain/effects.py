"""将桥接返回的中文界面文本解析为数值.

这是一种临时方案: 游戏知道准确的伤害和格挡数值, 但 Mod 只将其渲染为文本,
因此这里使用正则提取. 它适用于初始卡组和大多数基础牌,
但可能误读条件效果 ("如果...") 或动态数值 ("X 点").

更合适的方式是在 Mod 侧增加结构化数值字段.
补丁接入后, ``parse_card`` 优先使用结构化字段, 文本解析仅作为兜底.
规划器无需关心数值来自哪里.
"""

from __future__ import annotations

import re
from typing import Any

# 造成 6 点伤害 / 造成3点伤害3次 / 造成 8 点伤害. 给予 2 层易伤.
_DAMAGE = re.compile(r"造成\s*(\d+)\s*点伤害(?:[\s，,]*(\d+)\s*次)?")
_BLOCK = re.compile(r"获得\s*(\d+)\s*点格挡")
_VULN = re.compile(r"给予\s*(\d+)\s*层易伤")
_WEAK = re.compile(r"给予\s*(\d+)\s*层虚弱")
_DRAW = re.compile(r"抽\s*(\d+)\s*张牌")
_ENERGY = re.compile(r"获得\s*(\d+)\s*点能量")
_HEAL = re.compile(r"回复\s*(\d+)\s*点(?:生命|最大生命值)")
_STRENGTH_GAIN = re.compile(r"获得\s*(\d+)\s*点力量")
# 敏捷药水: "获得2点敏捷。" 敏捷提升本回合每张牌的格挡.
_DEXTERITY_GAIN = re.compile(r"获得\s*(\d+)\s*点敏捷")
# 应急按钮: "你在接下来的2回合内无法再从卡牌中获得格挡."
_NO_BLOCK_TURNS = re.compile(r"接下来的\s*(\d+)\s*回合内无法再从卡牌中获得格挡")
# 失去生命作为费用: 放血 "失去3点生命。"
_HP_COST = re.compile(r"失去\s*(\d+)\s*点生命")
# 覆甲: "获得4层覆甲。" 回合结束时按层数获得格挡.
_PLATED = re.compile(r"获得\s*(\d+)\s*层覆甲")
# 后续回合的固定收益: "在接下来的2个回合开始时，获得5点格挡。"
_FUTURE_BLOCK = re.compile(r"接下来的\s*(\d+)\s*个回合开始时[^。]*?获得\s*(\d+)\s*点格挡")
# 随机消耗: "随机消耗1张牌。" 无法选择烧哪张, 只能决定要不要冒这个险.
_RANDOM_EXHAUST = re.compile(r"随机消耗\s*(\d+)\s*张牌")
# 全身撞击: "造成你当前格挡值的伤害。" 伤害取决于打出时的格挡, 不能用固定值.
_BODY_SLAM = re.compile(r"当前格挡值?\s*的伤害")
# 欺凌: "该敌人身上每有一层易伤就额外造成2点伤害。" 伤害随目标易伤层数成长.
_PER_VULNERABLE = re.compile(r"每有一层易伤就额外造成\s*(\d+)\s*点伤害")
# 灰烬打击: "你的消耗牌堆中每有一张牌，伤害增加3。"
_PER_EXHAUST = re.compile(r"消耗牌堆中每有一张牌[，,]?\s*伤害增加\s*(\d+)")
# 桥接对"条件伤害"牌会给 0, 这时回到描述里取基础伤害.
_DAMAGE_TEXT = re.compile(r"造成\s*(\d+)\s*点伤害")
# 每回合开始的效果 (绯红披风): "在你的回合开始时，失去1点生命并获得8点格挡。"
_PER_TURN_BLOCK = re.compile(
    r"回合开始时[^。]*?失去\s*(\d+)\s*点生命[^。]*?获得\s*(\d+)\s*点格挡")
# 药水描述会把"给予所有敌人 N 层"写成整句, 数字前允许有其他词.
_BIND_VULN = re.compile(r"(\d+)\s*层易伤")
_BIND_WEAK = re.compile(r"(\d+)\s*层虚弱")
# 能量药水的数量被渲染成图标: "获得[ironclad_energy_icon.png]...".
_ENERGY_ICON = "energy_icon"
# 瓶中精灵: "当你的生命值将被减少至0或以下时，改为丢弃这瓶药水，
# 然后将你回复到你最大生命值的30%。"
_DEATH_SAVE = re.compile(r"最大生命值的\s*(\d+)\s*%")


def parse_potion(description: str) -> dict[str, int]:
    """将药水描述解析为数值.

    药水只提供描述字符串, 因此与卡牌文本解析一样属于临时方案.
    支持影响生存和斩杀的伤害 / 格挡 / 治疗药水, 复杂效果按 0 处理.
    敏捷与力量同样影响数值结算, 因此单独解析:
    力量提高攻击伤害, 敏捷提高从卡牌获得的格挡.
    """
    desc = description or ""

    return {
        "damage": _first(_DAMAGE, desc),
        "block": _first(_BLOCK, desc),
        "heal": _first(_HEAL, desc),
        "strength": _first(_STRENGTH_GAIN, desc),
        "dexterity": _first(_DEXTERITY_GAIN, desc),
        "vulnerable": _first(_VULN, desc) or _first(_BIND_VULN, desc),
        "weak": _first(_WEAK, desc) or _first(_BIND_WEAK, desc),
        "draw": _first(_DRAW, desc),
        # 能量药水: 数值可能是 "获得2点能量", 也可能被渲染成能量图标.
        "energy": _first(_ENERGY, desc) or desc.count(_ENERGY_ICON),
        # 强化卡牌的药水 (如熔炉的祝福) 会改变手牌本身;
        # 其价值通过卡牌评分计算, 而非单独的效果数值.
        "upgrade_hand": 1 if ("升级" in desc and "手牌" in desc) else 0,
        # 复制效果: "本回合, 你打出的下一张牌会额外打出一次."
        "duplicate_next": 1 if ("额外打出一次" in desc or "打出两次" in desc) else 0,
        # 随机无色牌: "从3张随机无色牌中选择1张加入你的手牌."
        # 具体牌未知, 模拟器用一张平均牌代替 (见 combat_search.COLORLESS_CARD_*).
        "random_card": 1 if ("无色牌" in desc and "手牌" in desc) else 0,
        # 阵亡自动复活 (瓶中精灵): 生命降到 0 时改为回复最大生命值的 N%.
        # 这是被动效果, 不能主动使用, 但必须计入"这回合会不会死".
        "death_save": (_first(_DEATH_SAVE, desc)
                       if ("减少至0" in desc or "丢弃这瓶药水" in desc) else 0),
    }


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# 桥接把数值渲染成图标占位符, 模型读不到实际数字, 需要换成文字.
_ICONS = {
    "[ironclad_energy_icon.png]": "1点能量",
    "[silent_energy_icon.png]": "1点能量",
    "[regent_energy_icon.png]": "1点能量",
    "[star_icon.png]": "1点星",
}


def humanize(text: str) -> str:
    """把图标占位符换成可读文字, 供模型判断牌面效果.

    示例: "获得[ironclad_energy_icon.png][ironclad_energy_icon.png]"
    读作 "获得1点能量1点能量", 模型才能看出这是 2 费产能.
    """
    out = text or ""
    for icon, label in _ICONS.items():
        out = out.replace(icon, label)
    return out


def _first(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return _int(match.group(1)) if match else 0


def _field(card: dict[str, Any], key: str, pattern: re.Pattern[str]) -> int:
    """优先读取结构化字段, 缺失时才从描述文本提取.

    带补丁的 Mod 始终输出该字段, 没有对应变量时输出 0.
    因此应检查字段是否存在, 而不是值是否为真.
    必须信任 0, 避免将条件描述中的数字误当成实际效果.
    """
    if key in card and card[key] is not None:
        return _int(card[key])
    m = pattern.search(card.get("description") or "")
    return _int(m.group(1)) if m else 0


def parse_card(card: dict[str, Any]) -> dict[str, Any]:
    """提取一张手牌的数值.

    Mod 从游戏自身的 DynamicVars 输出 ``damage`` / ``block`` / ``hits`` /
    ``vulnerable`` / ``weak``. 旧版仅提供 ``description``, 下方解析逻辑仍支持该形状.
    ``strength`` / ``dexterity`` 目前没有结构化字段, 只能从描述中提取:
    能力牌的全部价值都在这里, 不解析就等于在模拟中空过.
    """
    desc = card.get("description") or ""
    # "在回合开始时，获得 1 点能量" 是每回合生效, 不是打出时立即获得;
    # 能量在描述里可能写成图标 (energy_icon), 因此两种写法都要认.
    per_turn = "回合开始时" in desc and ("能量" in desc or "energy_icon" in desc)
    energy_now = 0 if per_turn else (_field(card, "energy", _ENERGY) or desc.count("energy_icon"))
    future = _FUTURE_BLOCK.search(desc)
    future_block_turns = _int(future.group(1)) if future else 0
    future_block_per_turn = _int(future.group(2)) if future else 0
    # 每回合开始的格挡 (绯红披风): 不是打出时获得, 因此不计入当回合 block.
    per_turn_block_match = _PER_TURN_BLOCK.search(desc)
    per_turn_hp_cost = _int(per_turn_block_match.group(1)) if per_turn_block_match else 0
    per_turn_block = _int(per_turn_block_match.group(2)) if per_turn_block_match else 0

    damage = _field(card, "damage", _DAMAGE)
    block = _field(card, "block", _BLOCK)
    hits = _int(card.get("hits"), 0) if "hits" in card else 0

    # 条件伤害牌 (欺凌/灰烬打击/全身撞击): 桥接按"当前条件"算出 0,
    # 基础伤害要回到描述里取, 否则会被当成 0 伤牌永远不打.
    conditional = (_BODY_SLAM.search(desc) or _PER_VULNERABLE.search(desc)
                   or _PER_EXHAUST.search(desc))
    if conditional and not damage:
        m = _DAMAGE_TEXT.search(desc)
        if m:
            damage = _int(m.group(1))

    if "damage" not in card:
        m = _DAMAGE.search(desc)
        if m and m.group(2):
            hits = _int(m.group(2))
    hits = max(1, hits or 1)

    return {
        "index": _int(card.get("index")),
        "id": card.get("id") or "",
        "name": card.get("name") or "",
        "cost": _int(card.get("cost"), 1),
        "type": (card.get("type") or "").lower(),
        "target_type": card.get("target_type") or "",
        # 星星是储君的资源, 当前未建模该资源,
        # 因此消耗星星的卡牌应视为不可打出, 避免尝试操作.
        "star_cost": _int(card.get("star_cost"), 0),
        "can_play": bool(card.get("can_play", True)),
        "damage": damage,
        "hits": hits,
        # 每回合效果的格挡不是即时格挡; 没有这层区分会把它当成"打出时获得".
        "block": 0 if per_turn_block else block,
        "vulnerable": _field(card, "vulnerable", _VULN),
        "weak": _field(card, "weak", _WEAK),
        "draw": _field(card, "draw", _DRAW),
        # 能量可能是结构化字段, 也可能被渲染成图标; 每个能量图标算 1 点.
        "energy": energy_now,
        # 每回合开始获得的能量 (薪火之源): 价值在后续回合, 由搜索折算.
        "per_turn_energy": (desc.count("energy_icon") or _int(card.get("energy"), 0) or 1)
        if per_turn else 0,
        # 以生命为费用的牌 (放血): 掉血必须计入, 否则 0 费产能会被高估.
        "hp_cost": _first(_HP_COST, desc),
        # 覆甲: 回合结束时按层数获得格挡.
        "plated": _first(_PLATED, desc),
        # 全身撞击: 伤害等于打出时的格挡, 由搜索在结算时计算.
        "body_slam": bool(_BODY_SLAM.search(desc)),
        # 欺凌 / 灰烬打击: 伤害随易伤层数或消耗堆成长.
        "per_vulnerable": _first(_PER_VULNERABLE, desc),
        "per_exhaust": _first(_PER_EXHAUST, desc),
        # 后续回合的格挡 (坚韧之环): 回合数与每回合格挡分开记, 由搜索按剩余回合折算.
        "future_block_turns": future_block_turns,
        "future_block_per_turn": future_block_per_turn,
        # 自身消耗 (战栗/契约终章): 影响灰烬打击的成长, 也影响本场牌堆.
        "exhaust": "消耗。" in desc,
        # 随机消耗 (未升级的坚毅): 可能烧掉手牌里的核心牌, 需要计入代价.
        "random_exhaust": _first(_RANDOM_EXHAUST, desc),
        # 每回合开始的格挡与其生命代价 (绯红披风).
        "per_turn_block": per_turn_block,
        "per_turn_hp_cost": per_turn_hp_cost,
        # 能力牌 (如燃烧) 的数值只在描述里: "获得2点力量。"
        # Mod 尚未输出结构化字段, 因此这里始终尝试解析, 否则能力牌在模拟中等同空过.
        "strength": _field(card, "strength", _STRENGTH_GAIN),
        "dexterity": _field(card, "dexterity", _DEXTERITY_GAIN),
        "no_block_turns": _first(_NO_BLOCK_TURNS, desc),
        "unplayable_reason": card.get("unplayable_reason"),
    }


def parse_intent(intent: dict[str, Any]) -> tuple[int, int]:
    """返回敌人意图的 (damage, hits)."""
    if intent.get("damage") is not None:
        return _int(intent.get("damage")), max(1, _int(intent.get("hits"), 1))

    if (intent.get("type") or "").lower() != "attack":
        return 0, 1

    label = str(intent.get("label") or "").strip()
    if label.isdigit():
        return _int(label), 1

    m = _DAMAGE.search(intent.get("description") or "")
    if m:
        return _int(m.group(1)), (_int(m.group(2), 1) if m.group(2) else 1)
    return 0, 1


# "5×3" / "5x3" / "5*3": 标签里的单次伤害与次数.
_MULTI = re.compile(r"(\d+)\s*[×xX*]\s*(\d+)")


def resolved_attack(intent: dict[str, Any]) -> tuple[int, int]:
    """返回敌人攻击的 (单次伤害, 次数), 优先使用游戏结算后的显示值.

    ``damage`` / ``hits`` 字段是基础值, 不含敌方力量与虚弱修正:
    例如基础 3 点、力量 2 点、攻击 3 次的意图会显示为 "5×3";
    反过来, 敌人被虚弱时基础 4 点会显示为 3 点.
    标签和描述是游戏结算后的文本, 因此有显示值时以显示值为准,
    两者都没有时才退回基础数值.
    """
    displays: list[tuple[int, int]] = []

    label = str(intent.get("label") or "").strip()
    m = _MULTI.search(label)
    if m:
        displays.append((_int(m.group(1)), max(1, _int(m.group(2)))))
    elif label.isdigit():
        displays.append((_int(label), 1))

    m = _DAMAGE.search(intent.get("description") or "")
    if m:
        displays.append((_int(m.group(1)), max(1, _int(m.group(2), 1) if m.group(2) else 1)))

    displays = [item for item in displays if item[0] > 0]
    if displays:
        return max(displays, key=lambda item: item[0] * item[1])
    return parse_intent(intent)


def incoming_damage(enemy: dict[str, Any]) -> int:
    total = 0
    for intent in enemy.get("intents") or []:
        if (intent.get("type") or "").lower() != "attack":
            continue
        dmg, hits = resolved_attack(intent)
        total += dmg * hits
    return total
