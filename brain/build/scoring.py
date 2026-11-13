"""共用卡牌评分：供离线构筑、卡牌包、商店及选牌入口复用现有启发式数值。

评分仅用于候选取舍，不定义卡牌效果，也不替代独立四维评估或完整战斗回测。
"""

from __future__ import annotations

from ..model import Card


RARITY_BONUS = {"Basic": 0.0, "Common": 0.3, "Uncommon": 0.9, "Rare": 1.8}
# 永远不主动拿的牌: 不是数值略差, 而是攻略与胜率数据都明确垫底的陷阱牌.
# 卡牌奖励的候选里会直接剔除, 模型看不到也就选不出来; 全被剔除时跳过奖励.
#
# 证据 (2026-07):
#   * Spire Codex 铁甲战士分级 https://spire-codex.com/guides/ironclad-tier-list
#     把 Break / Cinder 列为 The traps: 约 31% 胜率, "looks like removal support
#     and plays like a dead card" (按社区提交的对局统计).
#   * 铁斩波 (Iron Wave): 1 费 5 伤 5 挡, 补格挡不如防御/武装/岩石铠甲,
#     补伤害不如真攻击牌, 混进牌组只稀释关键牌 (本项目多次实盘验证).
NEVER_PICK = {
    "铁斩波", "IRON_WAVE",
    "破击", "BREAK",
    "余烬", "CINDER",
}
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
    # 群体伤害按 2 个目标折算: 大多数战斗 2~3 个敌人, 不折算会把彼岸咆哮、
    # 踩踏这类群体牌评成"3 费 16 伤"的笨重牌.
    targets = 2 if card.aoe else 1
    value = BASE_VALUE + card.damage * card.hits * targets + card.block * 0.9
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


# 同名卡收益递减: 第 2 张起打折, 第 4 张基本只剩"填充牌组"的作用.
DUPLICATE_FACTOR = (1.0, 0.55, 0.30, 0.15)


def deck_context(deck) -> dict:
    """牌组结构: 同名张数、各类型数量、格挡 / 过牌 / 引擎来源."""
    counts: dict[str, int] = {}
    for card in deck:
        counts[card.name] = counts.get(card.name, 0) + 1
    return {
        "counts": counts,
        "cards": len(deck),
        "attacks": sum(1 for c in deck if c.type.lower() == "attack"),
        "skills": sum(1 for c in deck if c.type.lower() == "skill"),
        "powers": sum(1 for c in deck if c.type.lower() == "power"),
        "blocks": sum(1 for c in deck if c.block > 0 or c.plated or c.per_turn_block),
        "draws": sum(1 for c in deck if c.draw > 0),
        "engines": sum(1 for c in deck
                       if c.per_turn_energy > 0 or c.strength > 0 or c.per_turn_strength > 0),
    }


def marginal_value(card: Card, deck) -> dict:
    """这张牌加进当前牌组值多少: 同名递减 + 类型饱和惩罚 + 缺口补强.

    单看 ``card_score`` 会把第二张飞剑回旋镖、第二张薪火之源评得和第一张一样好,
    实际它们只是重复占位; 这里把"已经有多少"算进去。
    """
    context = deck_context(deck)
    copies = context["counts"].get(card.name, 0)
    factor = DUPLICATE_FACTOR[min(copies, len(DUPLICATE_FACTOR) - 1)]
    notes: list[str] = []
    if copies:
        notes.append(f"已有 {copies} 张同名, 第 {copies + 1} 张按 {factor:.0%} 计")
    score = card_score(card) * factor

    kind = card.type.lower()
    if kind == "power" and context["powers"] >= 4:
        score *= 0.7
        notes.append("能力牌已有 4 张以上, 再拿容易卡手")
    if card.per_turn_energy and context["engines"] >= 2:
        score *= 0.6
        notes.append("已有能量/成长引擎, 第二张引擎收益递减")
    if kind == "attack" and context["attacks"] >= 8:
        score *= 0.85
        notes.append("攻击牌偏多, 需要的是格挡与过牌")
    if (card.block or card.plated) and context["blocks"] >= 6:
        score *= 0.85
        notes.append("格挡来源已够")
    if card.draw and context["draws"] == 0:
        score *= 1.3
        notes.append("牌组没有过牌, 抽牌价值更高")
    if card.per_turn_energy and context["engines"] == 0:
        score *= 1.2
        notes.append("牌组没有引擎, 能量引擎价值更高")

    return {"score": round(score, 2), "copies": copies, "notes": notes}
