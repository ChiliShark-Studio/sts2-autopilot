"""模拟牌组装配：从统一卡牌集创建独立的 DeckCard 实例与牌组。

本文件只适配模拟器的数据结构，不保存牌表或牌面数值。狂怒和恶魔形态使用共享效果，
其余模板通过数值字段结算；旋风斩声明 X 费和群体目标，实际费用与命中次数由战斗侧计算。
"""

from __future__ import annotations

from ..cards import find_card
from .engine import DeckCard


def card(name: str, upgraded: bool = False) -> DeckCard:
    definition = find_card(name, upgraded=upgraded)
    if definition is None:
        raise KeyError(f"未收录的卡牌: {name}{'+' if upgraded else ''}")
    fields = dict(definition.simulation_fields())
    if definition.effects:
        return DeckCard(name=definition.name, id=definition.id, target="Self", definition=definition, **fields)
    fields.setdefault("target", "Self" if fields.get("block") and not fields.get("damage") else "AnyEnemy")
    return DeckCard(name=definition.name, id=definition.name, **fields)


def deck(spec: dict[str, int]) -> list[DeckCard]:
    """按 {牌名: 张数} 装配牌组，每张牌保留独立的战斗实例。"""
    return [card(name) for name, count in spec.items() for _ in range(count)]
