"""已核对的战斗升级规则，供搜索、选牌与模拟共用。"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from .model import Card


@lru_cache(maxsize=1)
def _rules() -> dict[str, tuple[Card, Card]]:
    """只缓存静态图鉴；不缓存手牌、战斗或升级选择。"""
    path = Path(__file__).resolve().parents[1] / "data/cards/ironclad.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rules = {}
    for name in ("打击", "防御", "痛击", "耸肩无视", "武装", "旋风斩"):
        base = Card.parse(data[name]["base"])
        upgraded = Card.parse(data[name]["upgraded"])
        rules[name] = rules[base.id] = (base, upgraded)
    return rules


def upgrade(card: Card) -> Card:
    """返回有可信数值的升级版本；未知规则保持原样，不猜增量。"""
    if card.upgraded or card.name.endswith("+"):
        return card
    rule = _rules().get(card.id) or _rules().get(card.name)
    changes = {}
    if rule:
        base, upgraded = rule
        for key in ("cost", "damage", "block", "hits", "vulnerable", "weak", "draw", "energy"):
            # 保留已经存在的附魔与临时费用修正，仅叠加图鉴中的升级差值。
            changes[key] = max(0, getattr(card, key) + getattr(upgraded, key) - getattr(base, key))
        changes.update(upgrade_hand=upgraded.upgrade_hand, upgrade_all=upgraded.upgrade_all)
    for key in ("damage", "block"):
        exact = getattr(card, f"{key}_up")
        if exact is not None:
            changes[key] = exact
    if not changes:
        return card
    name = card.name or (rule[0].name if rule else "")
    return replace(card, **changes, name=f"{name}+" if name else "",
                   upgraded=True, damage_by_target={})


def upgrade_gain(before: Card, after: Card, block_weight: float, energy_value: float = 6.0) -> float:
    """沿用现有搜索的价值单位，比较已知升级增量，不代表实战保证。"""
    damage = after.damage * after.hits - before.damage * before.hits
    gain = damage + (after.block - before.block) * block_weight
    gain += (after.vulnerable - before.vulnerable) * 3 + (after.weak - before.weak) * 2
    gain += (after.draw - before.draw) * 2
    gain += ((after.energy - before.energy) + (before.cost - after.cost)) * energy_value
    return max(0.0, gain)


def choose_upgrade(cards: list[Card], block_weight: float, energy_value: float = 6.0) -> int | None:
    """按升级增量选择一张牌；同分保持输入顺序，调用方自行处理未知候选。"""
    gains = [(i, upgrade_gain(card, improved, block_weight, energy_value))
             for i, card in enumerate(cards) if (improved := upgrade(card)) is not card]
    return max(gains, key=lambda item: item[1])[0] if gains else None
