"""共用卡牌评分：供离线构筑、卡牌包、商店及选牌入口复用现有启发式数值。

评分仅用于候选取舍，不定义卡牌效果，也不替代独立四维评估或完整战斗回测。
"""

from __future__ import annotations

from ..model import Card


RARITY_BONUS = {"Basic": 0.0, "Common": 0.3, "Uncommon": 0.9, "Rare": 1.8}
# 依靠效果发挥价值的牌 (能力牌, 运转核心) 也需要最低评分,
# 否则会得到 0 分, 被轻易丢弃或变化.
BASE_VALUE = 1.0
TYPE_BONUS = {"power": 1.5}


def card_score(card: Card) -> float:
    """按每点能量的收益评分, 加少量稀有度奖励, 刻意保持简单.

    能力牌的成长 (力量 / 敏捷 / 覆甲) 没有直接的伤害或格挡数值,
    只看数值会把它们评成 0 分, 进而被弃牌和"变化"轻易丢掉.
    以生命为费用的牌则要扣除代价, 避免把掉血当成免费.
    """
    cost = max(1, card.cost)
    value = BASE_VALUE + card.damage * card.hits + card.block * 0.9
    value += card.vulnerable * 3 + card.weak * 2 + card.draw * 2 + card.energy * 3
    value += card.strength * 4 + card.dexterity * 3 + card.plated * 2
    value -= card.hp_cost * 1.5
    value += TYPE_BONUS.get(card.type.lower(), 0.0)
    return value / cost + RARITY_BONUS.get(card.rarity, 0.0)


# 决定弃牌时应更重视稀有度: 没有明显伤害或格挡数值的 Rare 牌
# 通常是运转核心, 不应简单视为差牌.
KEEP_WEIGHT = {"Basic": 0.0, "Common": 0.5, "Uncommon": 2.0, "Rare": 5.0}


def discard_score(card: Card) -> float:
    """分数越低, 越适合弃掉."""
    return card_score(card) + KEEP_WEIGHT.get(card.rarity, 1.0)


def _card_dict_value(card: dict) -> float:
    """评估原始卡牌字典的价值, 卡牌包条目只携带文本数值."""
    from ..effects import parse_card

    parsed = parse_card(card)
    cost = max(1, parsed["cost"])
    value = parsed["damage"] * parsed["hits"] + parsed["block"] * 0.9
    value += parsed["vulnerable"] * 3 + parsed["weak"] * 2 + parsed["draw"] * 2
    value += (parsed.get("strength", 0) * 4 + parsed.get("dexterity", 0) * 3
              + parsed.get("plated", 0) * 2 - parsed.get("hp_cost", 0) * 1.5)
    return value / cost + RARITY_BONUS.get(card.get("rarity") or "", 0.0)
