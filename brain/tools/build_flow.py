"""Build tool: rewards, card picks, and (for now) nothing else.

Two facts drive the logic here, both learned from a live run:

* a card reward entry is **not consumed** by ``skip_card_reward`` -- it stays in
  the reward list, so re-claiming it reopens the picker and looks like a hang.
  Correct order is: claim real loot, open the card once, pick, then proceed.
* an unhandled build screen (shop, event, rest) is left alone.  Returning no
  action stops the loop for one manual look instead of clicking blind.
"""

from __future__ import annotations

import re
from typing import Optional

from ..actions import (
    AdvanceDialogue,
    ChooseEventOption,
    ClaimReward,
    ClaimTreasureRelic,
    ConfirmBundleSelection,
    Proceed,
    RestOption,
    SelectBundle,
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


CARD_SYSTEM_PROMPT = """你是《杀戮尖塔2》的构筑助手。为当前卡组从卡牌奖励里选一张，或跳过。

判断原则：
- 卡组质量比数量重要：没有明显补强的牌就跳过（choice=-1）。
- 缺格挡补格挡，缺输出补输出；基础牌已经够多时优先功能牌。
- 纯数值小、与卡组无配合的牌不要拿。
- 缩放牌（伤害随牌组变化）、能力牌通常比同费用的白板强。

只输出 JSON：{"choice": <index 或 -1>, "reason": "<40字内>"}
"""


def _card_dict_value(card: dict) -> float:
    """Value of a raw card dict (bundle entries only carry the text stats)."""
    from ..effects import parse_card

    parsed = parse_card(card)
    cost = max(1, parsed["cost"])
    value = parsed["damage"] * parsed["hits"] + parsed["block"] * 0.9
    value += parsed["vulnerable"] * 3 + parsed["weak"] * 2 + parsed["draw"] * 2
    return value / cost + RARITY_BONUS.get(card.get("rarity") or "", 0.0)


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


_HP_LOSS = re.compile(r"失去\s*(\d+)\s*点(?:最大)?生命")
_MAX_HP_LOSS = re.compile(r"失去\s*(\d+)\s*点最大生命")
_HEAL_EVENT = re.compile(r"回复\s*(\d+)\s*点生命")
_MAX_HP_GAIN = re.compile(r"获得\s*(\d+)\s*点最大生命")
_LEAVE_HINTS = ("放弃", "离开", "结束", "不读了")

# Never let an event put us below these.
EVENT_HP_FLOOR = 15
EVENT_MAX_HP_LOSS_RATIO = 0.15
EVENT_COST_WEIGHT = 1.5
EVENT_UPGRADE_WEIGHT = 4.0
EVENT_CURSE_WEIGHT = 8.0


def _first_int(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def _event_score(option: dict, hp: int, max_hp: int) -> Optional[float]:
    """Score an event option, or None when it is too dangerous to take.

    Events are where the bot bled out: 真理石板 charges escalating max-HP costs
    (3, 6, 12, 24, 45) and the old policy just always picked index 0.
    """
    desc = option.get("description") or ""
    title = option.get("title") or ""
    text = f"{title} {desc}"

    if any(hint in text for hint in _LEAVE_HINTS):
        return 0.0

    max_hp_loss = _first_int(_MAX_HP_LOSS, text)
    hp_loss = _first_int(_HP_LOSS, text) - max_hp_loss
    heal = _first_int(_HEAL_EVENT, text)
    max_hp_gain = _first_int(_MAX_HP_GAIN, text)
    curse = 1 if "诅咒" in text else 0
    upgrades = 2 if "所有" in text and "升级" in text else (1 if "升级" in text else 0)

    # Hard safety gates.
    if hp - hp_loss <= EVENT_HP_FLOOR:
        return None
    if max_hp_loss and max_hp - max_hp_loss < max_hp * 0.5:
        return None
    if max_hp_loss and max_hp_loss > max_hp * EVENT_MAX_HP_LOSS_RATIO:
        return None

    return (
        (heal + max_hp_gain) * 1.5
        + upgrades * EVENT_UPGRADE_WEIGHT
        - (hp_loss + max_hp_loss) * EVENT_COST_WEIGHT
        - curse * EVENT_CURSE_WEIGHT
    )


class BuildFlowTool:
    name = "build_flow"

    def __init__(self, client=None) -> None:
        # Optional LLM for card picks: the heuristic cannot price scaling cards
        # (完美打击, 破灭) and therefore kept taking bland stat cards like 铁斩波.
        self.client = client
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
        if request.scene == "bundle_select":
            return self._bundle(request)
        # shop / treasure: deliberately unimplemented.
        return Decision(action=None, reason=f"未实现的界面: {request.scene}", source=Source.NONE, tool=self.name)

    def _card_reward(self, request: DecisionRequest) -> Decision:
        cards = request.state.card_reward
        if not cards:
            return Decision(action=SkipCardReward(), reason="没有可选卡", source=Source.HEURISTIC, tool=self.name)

        if self.client is not None:
            choice, reason, extra = self._llm_card_choice(request)
            if choice is None:
                # -1 means the model wants to skip; anything invalid -> heuristic.
                pass
            elif choice == -1:
                self._pending_card_open = True
                return Decision(action=SkipCardReward(), reason=reason or "跳过（模型）",
                                source=Source.LLM, tool=self.name, extra=extra)
            else:
                self._pending_card_open = True
                return Decision(action=SelectCardReward(choice), reason=reason,
                                source=Source.LLM, tool=self.name, extra=extra)

        best = max(cards, key=lambda c: (card_score(c), -c.index))
        score = card_score(best)
        if score < SKIP_THRESHOLD:
            self._pending_card_open = True
            return Decision(action=SkipCardReward(), reason=f"最高分 {score:.1f} 太低", source=Source.HEURISTIC, tool=self.name)
        self._pending_card_open = True
        return Decision(action=SelectCardReward(best.index), reason=f"选 {best.name}（{score:.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

    def _llm_card_choice(self, request: DecisionRequest) -> tuple[Optional[int], str, dict]:
        """Ask the model to pick a card (or -1 to skip). Returns (None, _) on failure."""
        state = request.state
        player = state.player
        candidates = state.card_reward
        valid = {c.index for c in candidates}

        deck = "、".join(c.name for c in player.deck[:40]) or "空"
        lines = [
            f"当前：{player.character} 第{state.run.floor}层 血量{player.hp}/{player.max_hp} 金币{player.gold}",
            f"卡组（{len(player.deck)}张）：{deck}",
            "",
            "候选卡牌：",
        ]
        for card in candidates:
            lines.append(
                f"- index={card.index} {card.name} | 费用{card.cost} | {card.type} | "
                f"{card.rarity} | {card.description}"
            )
        lines.append("")
        lines.append('只输出 JSON：{"choice": <index 或 -1 表示跳过>, "reason": "<40字内>"}')

        reply = self.client.json_chat(CARD_SYSTEM_PROMPT, "\n".join(lines))
        if reply is None:
            return None, "", {}
        extra = {"llm": dict(self.client.last_meta or {})}
        raw = reply.get("choice")
        try:
            choice = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None, "", {}
        if choice == -1:
            return -1, str(reply.get("reason") or "跳过"), extra
        if choice not in valid:
            return None, "", {}
        return choice, str(reply.get("reason") or ""), extra

    def _event(self, request: DecisionRequest) -> Decision:
        """Pick the option with the best gain/cost, never the one that bleeds us.

        真理石板 charges 3/6/12/24/45 max HP for upgrades; the old "always index
        0" rule walked that ladder down to 1 HP.
        """
        event = request.state.raw.get("event") or {}
        if event.get("in_dialogue"):
            return Decision(action=AdvanceDialogue(), reason="推进对话",
                            source=Source.SCRIPT, tool=self.name)
        options = [o for o in (event.get("options") or [])
                   if isinstance(o, dict) and not o.get("is_locked")]
        if not options:
            # Events rebuild their buttons between pages; empty is transient.
            return WAIT

        player = request.state.player
        scored: list[tuple[float, int, dict]] = []
        for option in options:
            score = _event_score(option, player.hp, player.max_hp)
            if score is None:
                continue
            scored.append((score, int(option.get("index", 0)), option))

        name = event.get("event_name") or "事件"
        if not scored:
            leave = next(
                (o for o in options
                 if any(h in f"{o.get('title', '')} {o.get('description', '')}" for h in _LEAVE_HINTS)),
                options[-1],
            )
            return Decision(action=ChooseEventOption(int(leave.get("index", 0))),
                            reason=f"{name}: 都太危险，离开（{leave.get('title', '')}）",
                            source=Source.HEURISTIC, tool=self.name)

        score, index, pick = max(scored, key=lambda item: (item[0], -item[1]))
        return Decision(action=ChooseEventOption(index),
                        reason=f"{name}: {pick.get('title', '')}（{score:+.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

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

    def _bundle(self, request: DecisionRequest) -> Decision:
        """Pick the card bundle with the best total value, then confirm."""
        bundle = request.state.raw.get("bundle_select") or {}
        if bundle.get("can_confirm") or bundle.get("preview_showing"):
            return Decision(action=ConfirmBundleSelection(), reason="确认卡牌包",
                            source=Source.SCRIPT, tool=self.name)

        bundles = [b for b in (bundle.get("bundles") or []) if isinstance(b, dict)]
        if not bundles:
            return WAIT

        def value(item: dict) -> float:
            return sum(_card_dict_value(c) for c in (item.get("cards") or []) if isinstance(c, dict))

        best = max(bundles, key=value)
        return Decision(action=SelectBundle(int(best.get("index", 0))),
                        reason=f"选价值最高的一包（{value(best):.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

    def _rewards(self, request: DecisionRequest) -> Decision:
        raw = request.state.raw.get("rewards") or {}
        items = request.state.rewards
        can_proceed = bool(raw.get("can_proceed"))
        player = request.state.player
        potions_full = len(player.potions) >= player.max_potion_slots

        # A potion reward cannot be claimed with full slots; re-claiming it is
        # how the loop got stuck, so treat it as unclaimable and move on.
        claimable = [
            r for r in items
            if r.type != "card" and not (r.type == "potion" and potions_full)
        ]
        if claimable:
            return Decision(action=ClaimReward(claimable[0].index), reason=f"领取 {claimable[0].type}",
                            source=Source.SCRIPT, tool=self.name)

        card_items = [r for r in items if r.type == "card"]
        if card_items:
            if self._pending_card_open and can_proceed:
                self._pending_card_open = False
                return Decision(action=Proceed(), reason="卡牌奖励已处理，离开",
                                source=Source.SCRIPT, tool=self.name)
            return Decision(action=ClaimReward(card_items[0].index), reason="打开选牌",
                            source=Source.SCRIPT, tool=self.name)

        if can_proceed:
            return Decision(action=Proceed(), reason="离开奖励界面", source=Source.SCRIPT, tool=self.name)
        return Decision(action=None, reason="奖励界面无可执行动作", source=Source.NONE, tool=self.name)
