"""Build tool: rewards, card picks, and (for now) nothing else.

Two facts drive the logic here, both learned from a live run:

* a card reward entry is **not consumed** by ``skip_card_reward`` -- it stays in
  the reward list, so re-claiming it reopens the picker and looks like a hang.
  Correct order is: claim real loot, open the card once, pick, then proceed.
* an unhandled build screen (shop, event, rest) is left alone.  Returning no
  action stops the loop for one manual look instead of clicking blind.
"""

from __future__ import annotations

from typing import Optional

from ..actions import (
    AdvanceDialogue,
    ChooseEventOption,
    ClaimReward,
    ClaimTreasureRelic,
    Proceed,
    RestOption,
    SelectCardReward,
    ShopPurchase,
    SkipCardReward,
)
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..model import Card

RARITY_BONUS = {"Basic": 0.0, "Common": 0.3, "Uncommon": 0.9, "Rare": 1.8}
SKIP_THRESHOLD = 2.0
# Cards whose value is in their effect, not their numbers (powers, engines),
# still need a floor -- otherwise they score 0 and get discarded/transformed.
BASE_VALUE = 1.0
TYPE_BONUS = {"power": 1.5}


def card_score(card: Card) -> float:
    """Value per energy, with a small rarity bonus. Crude by design."""
    cost = max(1, card.cost)
    value = BASE_VALUE + card.damage * card.hits + card.block * 0.9
    value += card.vulnerable * 3 + card.weak * 2 + card.draw * 2 + card.energy * 3
    value += TYPE_BONUS.get(card.type.lower(), 0.0)
    return value / cost + RARITY_BONUS.get(card.rarity, 0.0)


# Rarity matters much more when deciding what to throw away: a Rare with no
# visible numbers is usually an engine, not a bad card.
KEEP_WEIGHT = {"Basic": 0.0, "Common": 0.5, "Uncommon": 2.0, "Rare": 5.0}


def discard_score(card: Card) -> float:
    """Lower means 'safer to discard'."""
    return card_score(card) + KEEP_WEIGHT.get(card.rarity, 1.0)


# Shop behaviour: keep a gold reserve, and only buy a card that is clearly good.
SHOP_RESERVE = 40
SHOP_CARD_THRESHOLD = 3.0


def _shop_card_value(item: dict) -> float:
    """Score a shop card from its rendered description (no numeric fields there)."""
    from ..effects import parse_card

    parsed = parse_card({
        "description": item.get("card_description") or "",
        "cost": item.get("card_cost") or "1",
        "rarity": item.get("card_rarity") or "",
    })
    cost = max(1, parsed["cost"])
    value = parsed["damage"] * parsed["hits"] + parsed["block"] * 0.9
    value += parsed["vulnerable"] * 3 + parsed["weak"] * 2 + parsed["draw"] * 2
    return value / cost + RARITY_BONUS.get(item.get("card_rarity") or "", 0.0)


class BuildFlowTool:
    name = "build_flow"

    def __init__(self) -> None:
        self._pending_card_open = False
        self._shop_bought = False

    def decide(self, request: DecisionRequest) -> Decision:
        if request.scene == "card_reward":
            return self._card_reward(request)
        if request.scene == "rewards":
            return self._rewards(request)
        if request.scene == "event":
            return self._event(request)
        if request.scene in ("rest", "rest_site"):
            return self._rest(request)
        if request.scene == "treasure":
            return self._treasure(request)
        if request.scene == "shop":
            return self._shop(request)
        # shop / treasure: deliberately unimplemented.
        return Decision(action=None, reason=f"未实现的界面: {request.scene}", source=Source.NONE, tool=self.name)

    def _card_reward(self, request: DecisionRequest) -> Decision:
        cards = request.state.card_reward
        if not cards:
            return Decision(action=SkipCardReward(), reason="没有可选卡", source=Source.HEURISTIC, tool=self.name)
        best = max(cards, key=lambda c: (card_score(c), -c.index))
        score = card_score(best)
        if score < SKIP_THRESHOLD:
            self._pending_card_open = True
            return Decision(action=SkipCardReward(), reason=f"最高分 {score:.1f} 太低", source=Source.HEURISTIC, tool=self.name)
        self._pending_card_open = True
        return Decision(action=SelectCardReward(best.index), reason=f"选 {best.name}（{score:.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

    def _event(self, request: DecisionRequest) -> Decision:
        event = request.state.raw.get("event") or {}
        if event.get("in_dialogue"):
            return Decision(action=AdvanceDialogue(), reason="推进对话",
                            source=Source.SCRIPT, tool=self.name)
        options = [o for o in (event.get("options") or [])
                   if isinstance(o, dict) and not o.get("is_locked")]
        if not options:
            # Events rebuild their buttons between pages; empty is transient.
            return WAIT
        pick = options[0]
        name = event.get("event_name") or "事件"
        return Decision(action=ChooseEventOption(int(pick.get("index", 0))),
                        reason=f"{name}: {pick.get('title', '')}",
                        source=Source.SCRIPT, tool=self.name)

    def _rest(self, request: DecisionRequest) -> Decision:
        """Rest site: heal when hurt, otherwise upgrade. Also handles 'proceed'."""
        site = request.state.raw.get("rest_site") or {}
        if site.get("can_proceed"):
            return Decision(action=Proceed(), reason="离开篝火", source=Source.SCRIPT, tool=self.name)

        options = [o for o in (site.get("options") or [])
                   if isinstance(o, dict) and o.get("is_enabled")]
        if not options:
            return WAIT

        # The prompt naming is localised, but the ids are stable.
        want = "HEAL" if request.state.player.hp_ratio < 0.6 else "SMITH"
        pick = next((o for o in options if str(o.get("id")).upper() == want), options[0])
        reason = f"{pick.get('name', '')}: {pick.get('description', '')}"[:60]
        return Decision(action=RestOption(int(pick.get("index", 0))), reason=reason,
                        source=Source.HEURISTIC, tool=self.name)

    def _treasure(self, request: DecisionRequest) -> Decision:
        """Chest: take the relic, then leave. Relics only appear after the animation."""
        treasure = request.state.raw.get("treasure") or {}
        relics = [r for r in (treasure.get("relics") or []) if isinstance(r, dict)]
        if relics:
            pick = relics[0]
            return Decision(action=ClaimTreasureRelic(int(pick.get("index", 0))),
                            reason=f"拿取 {pick.get('name', '遗物')}",
                            source=Source.SCRIPT, tool=self.name)
        if treasure.get("can_proceed"):
            return Decision(action=Proceed(), reason="离开宝箱", source=Source.SCRIPT, tool=self.name)
        return WAIT

    def _shop(self, request: DecisionRequest) -> Decision:
        """Buy at most one good card, then leave. Keeps a gold reserve."""
        shop = request.state.raw.get("shop") or {}
        if shop.get("can_proceed") or self._shop_bought:
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

    def _rewards(self, request: DecisionRequest) -> Decision:
        raw = request.state.raw.get("rewards") or {}
        items = request.state.rewards
        can_proceed = bool(raw.get("can_proceed"))
        non_card = [r for r in items if r.type != "card"]
        if non_card:
            return Decision(action=ClaimReward(non_card[0].index), reason=f"领取 {non_card[0].type}",
                            source=Source.SCRIPT, tool=self.name)
        if items:
            if self._pending_card_open and can_proceed:
                self._pending_card_open = False
                return Decision(action=Proceed(), reason="卡牌奖励已处理，离开", source=Source.SCRIPT, tool=self.name)
            return Decision(action=ClaimReward(items[0].index), reason="打开选牌", source=Source.SCRIPT, tool=self.name)
        if can_proceed:
            return Decision(action=Proceed(), reason="离开奖励界面", source=Source.SCRIPT, tool=self.name)
        return Decision(action=None, reason="奖励界面无可执行动作", source=Source.NONE, tool=self.name)
