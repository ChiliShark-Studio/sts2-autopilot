"""商店决策：由构筑流程调用，比较购买、腾出药水槽位、删牌服务与离开候选。

本模块持有当前商店的购买标记，位置变化时由构筑流程重置；本局删牌次数保存在共享上下文。
模型候选选择复用构筑流程传入的选择函数，保持重试、停止与日志行为；不发送动作或处理后续删牌弹窗。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Optional

from ..actions import DiscardPotion, Proceed, ShopPurchase
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..strategy.context import StrategyContext
from .scoring import RARITY_BONUS


# 商店策略: 保留一定金币, 只购买明显有价值的卡牌.
SHOP_RESERVE = 40
SHOP_CARD_THRESHOLD = 3.0


def _shop_card_value(item: dict) -> float:
    """从商店卡牌的描述文本评分, 该处没有结构化数值字段."""
    from ..effects import parse_card

    parsed = parse_card({
        "description": item.get("card_description") or "",
        "cost": item.get("card_cost") or "1",
        "rarity": item.get("card_rarity") or "",
    })
    cost = max(1, parsed["cost"])
    value = parsed["damage"] * parsed["hits"] + parsed["block"] * 0.9
    value += parsed["vulnerable"] * 3 + parsed["weak"] * 2 + parsed["draw"] * 2
    value += (parsed.get("strength", 0) * 4 + parsed.get("dexterity", 0) * 3
              + parsed.get("plated", 0) * 2 - parsed.get("hp_cost", 0) * 1.5)
    return value / cost + RARITY_BONUS.get(item.get("card_rarity") or "", 0.0)


class ShopDecision:
    def __init__(self, context: StrategyContext, choose: Callable[..., Optional[Decision]], *, tool: str) -> None:
        self.context = context
        self._choose = choose
        self.name = tool
        self._shop_bought = False

    def reset(self) -> None:
        """位置变化时清除当前商店的购买标记，保留本局构筑上下文。"""
        self._shop_bought = False

    def decide(self, request: DecisionRequest) -> Decision:
        """LLM 每次选择一笔购买或离开；离线仍使用简单购卡策略。"""
        shop = request.state.raw.get("shop") or {}
        if not shop or shop.get("error"):
            return WAIT
        player = request.state.player
        full = len(player.potions) >= player.max_potion_slots
        items = [item for item in shop.get("items") or []
                 if isinstance(item, dict) and item.get("is_stocked") and item.get("can_afford")
                 and item.get("category") in {"card", "relic", "potion", "card_removal"}
                 and 0 <= int(item.get("price") or 0) <= player.gold]
        candidates = [(int(item.get("index", 0)), ShopPurchase(int(item.get("index", 0))), item)
                      for item in items if item.get("category") != "potion" or not full]
        # 商店库存展开时 proceed 可能未启用；桥接的 proceed 会先关闭库存再离开。
        candidates.append((-1, Proceed(), {"说明": "留钱并离开商店"}))
        if full and player.max_potion_slots > 0:
            potions = [item for item in items if item.get("category") == "potion"]
            if potions:
                for potion in player.potions:
                    candidates.append((-2 - potion.slot, DiscardPotion(potion.slot),
                                       {"说明": "先腾出槽位；下一步重新读取商品和金币后再决定购买",
                                        "discard": asdict(potion), "incoming_potions": potions}))
        # 不可购买的商品要显式告诉模型原因: 否则它会反复选择售罄的删牌服务.
        unavailable = []
        for item in shop.get("items") or []:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if index is None:
                continue
            reason = None
            if not item.get("is_stocked"):
                reason = "已售罄"
            elif item.get("category") == "potion" and full:
                reason = "药水槽已满"
            elif not item.get("can_afford") or int(item.get("price") or 0) > player.gold:
                reason = "金币不足"
            if reason:
                unavailable.append({"index": index, "category": item.get("category"),
                                    "price": item.get("price"), "reason": reason})
        decision = self._choose(
            request,
            "商店：比较买卡、遗物、药水、删牌与留钱；删牌需先购买服务再选择要删除的牌。"
            "优先级从高到低："
            "① 补齐 combos_missing 里的组合另一半，或补齐牌组完全缺失的主线引擎——"
            "这类是赢法本身，金币够就先拿，不要为了删牌推迟；"
            "② 删牌（牌组还有基础打击/防御时）；"
            "③ 能立刻提升当前主线的遗物（力量/易伤/攻击倍率、格挡/减伤、能量/抽牌）；"
            "④ 普通补强牌；⑤ 填充牌不买。"
            "删牌价格会随已删次数上涨（A0-5 为 75/100/125…，A6 起 100/150/200…），"
            "按标价判断值不值，不要按固定 75 金估算。"
            "买任何东西之后都要留下一次删牌的钱（situation.shop.next_removal_price）；"
            "金币不够同时买引擎和删牌时，先买引擎，下次再删。"
            "notes.shop_unavailable 列出买不了的商品及原因（售罄/金币不足/药水槽满），"
            "这些商品不在候选里，不要选择它们。",
            candidates,
            notes={"shop_unavailable": unavailable} if unavailable else None)
        if decision is not None:
            if isinstance(decision.action, ShopPurchase):
                self._shop_bought = True
                if self._is_removal(request, decision.action):
                    self.context.removals += 1
            return decision

        if self._shop_bought:
            self._shop_bought = False
            return Decision(action=Proceed(), reason="离开商店", source=Source.SCRIPT, tool=self.name)

        gold = request.state.player.gold
        best: Optional[tuple[float, dict]] = None
        for item in shop.get("items") or []:
            if not isinstance(item, dict):
                continue
            if item.get("category") != "card" or not item.get("is_stocked") or not item.get("can_afford"):
                continue
            price = int(item.get("price") or 0)
            if price > gold - SHOP_RESERVE:
                continue
            value = _shop_card_value(item)
            if value >= SHOP_CARD_THRESHOLD and (best is None or value > best[0]):
                best = (value, item)

        if best is not None:
            value, item = best
            self._shop_bought = True
            return Decision(action=ShopPurchase(int(item.get("index", 0))),
                            reason=f"买 {item.get('card_name')}（{value:.1f}，{item.get('price')}金）",
                            source=Source.HEURISTIC, tool=self.name)

        self._shop_bought = False
        return Decision(action=Proceed(), reason="没有值得买的，离开", source=Source.SCRIPT, tool=self.name)

    def _is_removal(self, request: DecisionRequest, action) -> bool:  # noqa: ANN001
        """判断这次购买是不是删牌服务 (用于累计涨价次数)."""
        index = getattr(action, "index", None)
        for item in (request.state.raw.get("shop") or {}).get("items") or []:
            if isinstance(item, dict) and item.get("index") == index:
                return item.get("category") == "card_removal"
        return False
