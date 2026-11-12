"""类型化动作, 结构参考 ``spirecomm/communication/action.py``.

每个动作提供原始字典载荷不具备的三项能力:

* 使用 ``to_payload`` 转换为桥接协议载荷.
* 使用 ``check`` 检查当前状态下是否合法, 提前发现过期索引, 避免服务端返回难以理解的错误.
* 生成可用于日志的动作描述.

子类自行定义 ``__init__``, 不依赖 dataclass 字段顺序:
基类的 ``kind`` 已有默认值, 在其后混入必填字段会导致 dataclass 报错.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .model import Card, GameState


class ActionError(ValueError):
    """动作在给定状态下不合法时抛出的异常."""


@dataclass(frozen=True)
class Action:
    kind: str = "noop"

    def to_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def check(self, state: GameState) -> None:
        """动作当前无法执行时抛出 ActionError."""

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
        if not self.card.can_play:
            raise ActionError(f"{self.card.name} is not playable right now")
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
class DiscardPotion(Action):
    slot: int = 0

    def __init__(self, slot: int) -> None:
        object.__setattr__(self, "kind", "discard_potion")
        object.__setattr__(self, "slot", slot)

    def check(self, state: GameState) -> None:
        if self.slot not in {p.slot for p in state.player.potions}:
            raise ActionError(f"药水槽位 {self.slot} 为空或不存在")

    def to_payload(self) -> dict[str, Any]:
        return {"action": "discard_potion", "slot": self.slot}

    def describe(self) -> str:
        return f"丢弃药水 槽位{self.slot}"


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
        # 桥接在这里使用 'card_index', 不同于 claim_reward 的 'index'.
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
    """在网格或手牌选择弹窗中选牌, 用于弃牌, 消耗等操作."""

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
    """战斗内的手牌选择使用独立的桥接动作.

    ``select_card`` 用于网格弹窗; 战斗内选牌 (生存者弃牌, 出牌时消耗等)
    通过 ``NPlayerHand`` 处理, 需要使用携带 ``card_index`` 的 ``combat_select_card``.
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
class SelectBundle(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "select_bundle")
        object.__setattr__(self, "index", index)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "select_bundle", "index": self.index}

    def describe(self) -> str:
        return f"选择卡牌包 {self.index}"


@dataclass(frozen=True)
class ConfirmBundleSelection(Action):
    kind: str = "confirm_bundle_selection"

    def to_payload(self) -> dict[str, Any]:
        return {"action": "confirm_bundle_selection"}

    def describe(self) -> str:
        return "确认卡牌包"


@dataclass(frozen=True)
class ShopPurchase(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "shop_purchase")
        object.__setattr__(self, "index", index)

    def to_payload(self) -> dict[str, Any]:
        return {"action": "shop_purchase", "index": self.index}

    def check(self, state: GameState) -> None:
        item = next((item for item in (state.raw.get("shop") or {}).get("items", [])
                     if isinstance(item, dict) and item.get("index") == self.index), None)
        if not item or not item.get("is_stocked") or not item.get("can_afford"):
            raise ActionError("商品已售罄、不可购买或金币不足")
        if int(item.get("price") or 0) > state.player.gold:
            raise ActionError("当前金币不足")
        if item.get("category") == "potion" and len(state.player.potions) >= state.player.max_potion_slots:
            raise ActionError("药水槽位已满")

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
class SelectRelic(Action):
    index: int = 0

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "kind", "select_relic")
        object.__setattr__(self, "index", index)

    def check(self, state: GameState) -> None:
        relics = (state.raw.get("relic_select") or {}).get("relics") or []
        if self.index not in {r.get("index") for r in relics if isinstance(r, dict)}:
            raise ActionError("遗物候选不存在")

    def to_payload(self) -> dict[str, Any]:
        return {"action": "select_relic", "index": self.index}

    def describe(self) -> str:
        return f"选择遗物 {self.index}"


@dataclass(frozen=True)
class SkipRelicSelection(Action):
    kind: str = "skip_relic_selection"

    def check(self, state: GameState) -> None:
        if not (state.raw.get("relic_select") or {}).get("can_skip"):
            raise ActionError("当前遗物选择不能跳过")

    def to_payload(self) -> dict[str, Any]:
        return {"action": "skip_relic_selection"}

    def describe(self) -> str:
        return "跳过遗物选择"


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
