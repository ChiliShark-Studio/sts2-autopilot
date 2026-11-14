"""卡牌解析体检: 找出"描述里有内容、但解析没接住"的牌.

背景: 桥接只给描述文本, 效果靠正则解析; 任何没覆盖到的机制都会让这张牌
在模型里变成白板。今天连续踩到五次: 残酷、覆甲、祭品抽牌、胆小、痛殴。

体检思路: 描述里出现某类效果的关键词, 但对应解析字段还是空的 → 报可疑。
它只是诊断, 允许误报 (例如文本提到"力量"但牌本身不提供力量),
目的是让人一眼看到"这张牌可能没建模"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 描述里出现这些关键词, 就检查对应的解析字段.
_ATTACK_TEXT = re.compile(r"造成\s*\d+\s*点伤害")
_HITS_TWICE = re.compile(r"伤害\s*两次|攻击\s*两次")
_BLOCK_TEXT = re.compile(r"获得\s*\d+\s*点格挡")
_DRAW_TEXT = re.compile(r"抽\s*[0-9一二两三四五六七八九十]+\s*张")
_ENERGY_TEXT = re.compile(r"获得\s*\d+\s*点能量|能量")
_STRENGTH_TEXT = re.compile(r"力量")
_DEXTERITY_TEXT = re.compile(r"敏捷")
_PLATED_TEXT = re.compile(r"覆甲")
_VULN_TEXT = re.compile(r"易伤")
_WEAK_TEXT = re.compile(r"虚弱")
_EXHAUST_TEXT = re.compile(r"消耗")


@dataclass(frozen=True)
class Warning:
    name: str
    hints: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "hints": list(self.hints)}


def audit_card(card) -> Warning | None:
    """单张牌体检; 没有可疑项返回 None."""
    desc = getattr(card, "description", "") or ""
    if not desc:
        return None
    hints: list[str] = []

    if _ATTACK_TEXT.search(desc) and not (card.damage or card.per_vulnerable
                                          or card.per_exhaust or card.body_slam):
        hints.append("描述有伤害, 但 damage 为 0")
    if _HITS_TWICE.search(desc) and card.hits < 2 and not card.hits_if_vulnerable:
        hints.append("描述写两段, 但 hits=1")
    if _BLOCK_TEXT.search(desc) and not (card.block or card.grants_block
                                         or card.plated or card.per_turn_block
                                         or card.block_per_attack):
        hints.append("描述有格挡, 但没有任何格挡字段")
    if _DRAW_TEXT.search(desc) and not card.draw:
        hints.append("描述有抽牌, 但 draw 为 0")
    if _ENERGY_TEXT.search(desc) and not (card.energy or card.per_turn_energy
                                          or card.energy_per_attack):
        hints.append("描述提到能量, 但 energy 为 0")
    if _STRENGTH_TEXT.search(desc) and not (
            card.strength or card.per_turn_strength or card.temporary_strength
            or getattr(card, "rupture", 0) or getattr(card, "enemy_strength", 0)
            or getattr(card, "enemy_strength_down", 0)):
        hints.append("描述提到力量, 但没有任何力量字段")
    if _DEXTERITY_TEXT.search(desc) and not card.dexterity:
        hints.append("描述提到敏捷, 但 dexterity 为 0")
    if _PLATED_TEXT.search(desc) and not card.plated:
        hints.append("描述提到覆甲, 但 plated 为 0")
    if _VULN_TEXT.search(desc) and not (
            card.vulnerable or card.per_vulnerable or card.hits_if_vulnerable
            or getattr(card, "cruelty", 0) or getattr(card, "draw_on_vulnerable", 0)
            or getattr(card, "vuln_damage_reduction", 0)):
        hints.append("描述提到易伤, 但没有任何易伤相关字段")
    if _WEAK_TEXT.search(desc) and not card.weak:
        hints.append("描述提到虚弱, 但 weak 为 0")
    # "每当有牌被消耗" / "消耗牌堆" 是触发式效果, 不是"这张牌会被消耗".
    if (_EXHAUST_TEXT.search(desc) and not (card.exhaust or card.random_exhaust
                                            or card.absorb_attack)
            and "每当" not in desc and "消耗牌堆" not in desc):
        hints.append("描述提到消耗, 但 exhaust 未标记")

    return Warning(card.name, tuple(hints)) if hints else None


def audit(cards) -> list[Warning]:
    """批量体检; 同名牌合并, 只保留第一次出现的问题."""
    seen: dict[str, Warning] = {}
    for card in cards:
        warning = audit_card(card)
        if warning is not None and warning.name not in seen:
            seen[warning.name] = warning
    return list(seen.values())
