"""Typed actions, mirroring ``spirecomm/communication/action.py``.

An action knows three things the old dict payloads did not:

* how to render itself onto the bridge protocol (``to_payload``);
* whether it is legal in the current state (``check``), so a stale index is
  caught before it is sent instead of coming back as a confusing server error;
* how to describe itself in a log line.

Subclasses set their own ``__init__`` rather than relying on dataclass field
order: the base class already has a defaulted ``kind`` field, and mixing
required fields after it is a dataclass error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .model import Card, GameState


class ActionError(ValueError):
    """Raised when an action is not legal in the given state."""


@dataclass(frozen=True)
class Action:
    kind: str = "noop"

    def to_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def check(self, state: GameState) -> None:
        """Raise ActionError if the action cannot be executed now."""

    def describe(self) -> str:
        return self.kind


@dataclass(frozen=True)
class PlayCard(Action):
    card: Card = None  # type: ignore[assignment]
    target: Optional[str] = None

    def __init__(self, card: Card, target: Optional[str] = None) -> None:
        object.__setattr__(self, "kind", "play_card")
        object.__setattr__(self, "card", card)
        object.__setattr__(self, "target", target)

    def check(self, state: GameState) -> None:
        if state.battle is None:
            raise ActionError("not in combat")
        if not state.battle.can_act:
            raise ActionError("combat not ready for action")
        if self.card.index < 0 or self.card.index >= len(state.battle.hand):
            raise ActionError(f"hand index {self.card.index} out of range")
        if self.card.cost > state.player.energy:
            raise ActionError(f"{self.card.name} costs {self.card.cost} > energy {state.player.energy}")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": "play_card", "card_index": self.card.index}
        if self.target:
            payload["target"] = self.target
        return payload

    def describe(self) -> str:
        return f"打出 {self.card.name}(费{self.card.cost})" + (f" -> {self.target}" if self.target else "")


@dataclass(frozen=True)
class EndTurn(Action):
    kind: str = "end_turn"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "end_turn"}

    def describe(self) -> str:
        return "结束回合"


@dataclass(frozen=True)
class UsePotion(Action):
    slot: int = 0
    target: Optional[str] = None

    def __init__(self, slot: int, target: Optional[str] = None) -> None:
        object.__setattr__(self, "kind", "use_potion")
        object.__setattr__(self, "slot", slot)
        object.__setattr__(self, "target", target)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": "use_potion", "slot": self.slot}
        if self.target:
            payload["target"] = self.target
        return payload

    def describe(self) -> str:
        return f"使用药水 槽位{self.slot}"


@dataclass(frozen=True)
class ChooseMapNode(Action):
    option: int = 0

    def __init__(self, option: int) -> None:
        object.__setattr__(self, "kind", "choose_map_node")
        object.__setattr__(self, "option", option)

    def check(self, state: GameState) -> None:
        if state.map is None:
            raise ActionError("not on the map")
        valid = {o.index for o in state.map.options}
        if self.option not in valid:
            raise ActionError(f"map option {self.option} not in {sorted(valid)}")

    def to_payload(self) -> dict[str, Any]:
        return {"action": "choose_map_node", "index": self.option}

    def describe(self) -> str:
        return f"选择地图节点 {self.option}"


@dataclass(frozen=True)
class ClaimReward(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "claim_reward")
        object.__setattr__(self, "index", index)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "claim_reward", "index": self.index}

    def describe(self) -> str:
        return f"领取奖励 {self.index}"


@dataclass(frozen=True)
class SelectCardReward(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "select_card_reward")
        object.__setattr__(self, "index", index)

    def check(self, state: GameState) -> None:
        valid = {c.index for c in state.card_reward}
        if self.index not in valid:
            raise ActionError(f"card reward {self.index} not in {sorted(valid)}")

    def to_payload(self) -> dict[str, Any]:
        # The bridge names this one 'card_index' (unlike claim_reward's 'index').
        return {"action": "select_card_reward", "card_index": self.index}

    def describe(self) -> str:
        return f"选择卡牌奖励 {self.index}"


@dataclass(frozen=True)
class SkipCardReward(Action):
    kind: str = "skip_card_reward"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "skip_card_reward"}

    def describe(self) -> str:
        return "跳过卡牌奖励"


@dataclass(frozen=True)
class Proceed(Action):
    kind: str = "proceed"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "proceed"}

    def describe(self) -> str:
        return "继续"


@dataclass(frozen=True)
class ChooseEventOption(Action):
    option: int = 0

    def __init__(self, option: int) -> None:
        object.__setattr__(self, "kind", "choose_event_option")
        object.__setattr__(self, "option", option)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "choose_event_option", "index": self.option}

    def describe(self) -> str:
        return f"事件选项 {self.option}"


@dataclass(frozen=True)
class SelectCard(Action):
    """Pick a card on a modal grid/hand selection screen (discard, exhaust...)."""

    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "select_card")
        object.__setattr__(self, "index", index)

    def check(self, state: GameState) -> None:
        picker = state.hand_select
        if picker is None:
            raise ActionError("no card selection screen")
        if self.index < 0 or self.index >= len(picker.cards):
            raise ActionError(f"select index {self.index} out of range ({len(picker.cards)} cards)")

    def to_payload(self) -> dict[str, Any]:
        return {"action": "select_card", "index": self.index}

    def describe(self) -> str:
        return f"选择第 {self.index} 张牌"


@dataclass(frozen=True)
class CombatSelectCard(Action):
    """Hand selection *during combat* uses a different bridge action.

    ``select_card`` targets the grid overlay; in-combat picks (Survivor's
    discard, exhaust-on-play) go through ``NPlayerHand`` and need
    ``combat_select_card`` with a ``card_index``.
    """

    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "combat_select_card")
        object.__setattr__(self, "index", index)

    def check(self, state: GameState) -> None:
        picker = state.hand_select
        if picker is None:
            raise ActionError("no card selection screen")
        if self.index < 0 or self.index >= len(picker.cards):
            raise ActionError(f"select index {self.index} out of range ({len(picker.cards)} cards)")

    def to_payload(self) -> dict[str, Any]:
        return {"action": "combat_select_card", "card_index": self.index}

    def describe(self) -> str:
        return f"战斗中选择第 {self.index} 张牌"


@dataclass(frozen=True)
class CombatConfirmSelection(Action):
    kind: str = "combat_confirm_selection"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "combat_confirm_selection"}

    def describe(self) -> str:
        return "确认选择（战斗）"


@dataclass(frozen=True)
class ConfirmSelection(Action):
    kind: str = "confirm_selection"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "confirm_selection"}

    def describe(self) -> str:
        return "确认选择"


@dataclass(frozen=True)
class CancelSelection(Action):
    kind: str = "cancel_selection"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "cancel_selection"}

    def describe(self) -> str:
        return "取消选择"


@dataclass(frozen=True)
class AdvanceDialogue(Action):
    kind: str = "advance_dialogue"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "advance_dialogue"}

    def describe(self) -> str:
        return "推进对话"


@dataclass(frozen=True)
class ShopPurchase(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "shop_purchase")
        object.__setattr__(self, "index", index)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "shop_purchase", "index": self.index}

    def describe(self) -> str:
        return f"购买商品 {self.index}"


@dataclass(frozen=True)
class ClaimTreasureRelic(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "claim_treasure_relic")
        object.__setattr__(self, "index", index)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "claim_treasure_relic", "index": self.index}

    def describe(self) -> str:
        return f"拿取遗物 {self.index}"


@dataclass(frozen=True)
class RestOption(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "choose_rest_option")
        object.__setattr__(self, "index", index)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "choose_rest_option", "index": self.index}

    def describe(self) -> str:
        return f"篝火选项 {self.index}"


@dataclass(frozen=True)
class MenuSelect(Action):
    option: str = ""

    def __init__(self, option: str) -> None:
        object.__setattr__(self, "kind", "menu_select")
        object.__setattr__(self, "option", option)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "menu_select", "option": self.option}

    def describe(self) -> str:
        return f"菜单: {self.option}"
