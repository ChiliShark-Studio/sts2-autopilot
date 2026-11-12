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
# 应急按钮: "你在接下来的2回合内无法再从卡牌中获得格挡."
_NO_BLOCK_TURNS = re.compile(r"接下来的\s*(\d+)\s*回合内无法再从卡牌中获得格挡")


def parse_potion(description: str) -> dict[str, int]:
    """将药水描述解析为数值.

    药水只提供描述字符串, 因此与卡牌文本解析一样属于临时方案.
    支持影响生存和斩杀的伤害 / 格挡 / 治疗药水, 复杂效果按 0 处理.
    """
    desc = description or ""

    return {
        "damage": _first(_DAMAGE, desc),
        "block": _first(_BLOCK, desc),
        "heal": _first(_HEAL, desc),
        "strength": _first(_STRENGTH_GAIN, desc),
        "vulnerable": _first(_VULN, desc),
        "weak": _first(_WEAK, desc),
        "draw": _first(_DRAW, desc),
        # 强化卡牌的药水 (如熔炉的祝福) 会改变手牌本身;
        # 其价值通过卡牌评分计算, 而非单独的效果数值.
        "upgrade_hand": 1 if ("升级" in desc and "手牌" in desc) else 0,
        # 复制效果: "本回合, 你打出的下一张牌会额外打出一次."
        "duplicate_next": 1 if ("额外打出一次" in desc or "打出两次" in desc) else 0,
    }


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


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
    """
    desc = card.get("description") or ""

    damage = _field(card, "damage", _DAMAGE)
    block = _field(card, "block", _BLOCK)
    hits = _int(card.get("hits"), 0) if "hits" in card else 0

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
        "block": block,
        "vulnerable": _field(card, "vulnerable", _VULN),
        "weak": _field(card, "weak", _WEAK),
        "draw": _field(card, "draw", _DRAW),
        "energy": _field(card, "energy", _ENERGY),
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


def incoming_damage(enemy: dict[str, Any]) -> int:
    total = 0
    for intent in enemy.get("intents") or []:
        dmg, hits = parse_intent(intent)
        total += dmg * hits
    return total
