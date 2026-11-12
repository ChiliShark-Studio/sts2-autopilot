"""选牌工具测试: 静默猎手的生存者会在战斗中要求弃牌."""

from __future__ import annotations

import unittest

from brain.actions import CombatConfirmSelection, CombatSelectCard, ConfirmSelection, SelectCard
from brain.contracts import DecisionKind, DecisionRequest
from brain.model import GameState
from brain.tools.select_tool import SelectTool

DISCARD = {
    "state_type": "hand_select",
    "hand_select": {
        "mode": "simple_select",
        "prompt": "选择1张牌来丢弃。",
        "can_confirm": False,
        "cards": [
            {"index": 0, "name": "防御", "cost": "1", "block": 5},
            {"index": 1, "name": "打击", "cost": "1", "damage": 6},
        ],
    },
    "player": {"hp": 50, "max_hp": 70},
}

UPGRADE = {
    "state_type": "hand_select",
    "hand_select": {
        "mode": "simple_select",
        "prompt": "选择1张牌来升级。",
        "can_confirm": False,
        "cards": [
            {"index": 0, "name": "防御", "cost": "1", "block": 5},
            {"index": 1, "name": "打击", "cost": "1", "damage": 6},
        ],
    },
    "player": {"hp": 50, "max_hp": 70},
}


def request(payload: dict) -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.SELECT, state=GameState.parse(payload), scene="hand_select")


class SelectToolTests(unittest.TestCase):
    def test_discard_picks_weakest_card(self):
        decision = SelectTool().decide(request(DISCARD))
        self.assertIsInstance(decision.action, SelectCard)
        self.assertEqual(decision.action.index, 0)  # 防御 5 < 打击 6

    def test_upgrade_picks_strongest_card(self):
        decision = SelectTool().decide(request(UPGRADE))
        self.assertIsInstance(decision.action, SelectCard)
        self.assertEqual(decision.action.index, 1)  # 打击

    def test_confirms_when_screen_asks(self):
        payload = {"state_type": "hand_select", "player": {"hp": 50, "max_hp": 70},
                   "hand_select": {"prompt": "选择", "can_confirm": True,
                                   "cards": [{"index": 0, "name": "打击", "damage": 6}]}}
        decision = SelectTool().decide(request(payload))
        self.assertIsInstance(decision.action, ConfirmSelection)

    def test_in_combat_uses_hand_selection_action(self):
        payload = {**DISCARD, "battle": {"turn": "player", "ready_for_action": False, "enemies": []}}
        decision = SelectTool().decide(request(payload))
        self.assertIsInstance(decision.action, CombatSelectCard)
        self.assertEqual(decision.action.to_payload(), {"action": "combat_select_card", "card_index": 0})

    def test_in_combat_confirm_uses_own_action(self):
        payload = {"state_type": "hand_select", "player": {"hp": 50, "max_hp": 70},
                   "battle": {"turn": "player", "ready_for_action": False, "enemies": []},
                   "hand_select": {"prompt": "选择", "can_confirm": True,
                                   "cards": [{"index": 0, "name": "打击", "damage": 6}]}}
        decision = SelectTool().decide(request(payload))
        self.assertIsInstance(decision.action, CombatConfirmSelection)

    def test_skips_already_selected_cards(self):
        payload = {"state_type": "card_select", "player": {"hp": 50, "max_hp": 70},
                   "card_select": {"screen_type": "transform", "prompt": "选择1张牌来变化。",
                                   "can_confirm": False, "selected_count": 1,
                                   "cards": [
                                       {"index": 0, "name": "防御", "cost": "1", "block": 5, "is_selected": True},
                                       {"index": 1, "name": "打击", "cost": "1", "damage": 6, "is_selected": False},
                                   ]}}
        decision = SelectTool().decide(request(payload))
        self.assertIsInstance(decision.action, SelectCard)
        self.assertEqual(decision.action.index, 1)  # 不能重复点击已选中的卡牌

    def test_confirms_when_preview_is_showing(self):
        payload = {"state_type": "card_select", "player": {"hp": 50, "max_hp": 70},
                   "card_select": {"screen_type": "upgrade", "prompt": "选择1张牌来升级。",
                                   "preview_showing": True, "can_confirm": False,
                                   "cards": [{"index": 0, "name": "打击", "damage": 6}]}}
        decision = SelectTool().decide(request(payload))
        self.assertIsInstance(decision.action, ConfirmSelection)

    def test_empty_picker_waits(self):
        payload = {"state_type": "hand_select", "player": {"hp": 50, "max_hp": 70},
                   "hand_select": {"prompt": "", "can_confirm": False, "cards": []}}
        decision = SelectTool().decide(request(payload))
        self.assertIsNone(decision.action)


class PutOnDrawPileTests(unittest.TestCase):
    """头槌: 选择一张牌放到抽牌堆顶, 应该挑最想要的牌而不是最差的."""

    def test_picks_best_card_for_draw_pile_top(self):
        raw = {
            "state_type": "hand_select",
            "hand_select": {
                "prompt": "选择一张牌放到你的抽牌堆顶。",
                "cards": [
                    {"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1},
                    {"index": 1, "name": "痛击", "cost": "2", "damage": 8, "hits": 1,
                     "vulnerable": 2},
                ],
            },
            "player": {"hp": 40, "max_hp": 80},
        }
        decision = SelectTool().decide(
            DecisionRequest(kind=DecisionKind.SELECT, state=GameState.parse(raw)))
        self.assertEqual(decision.action.index, 1)
        self.assertIn("最高", decision.reason)


if __name__ == "__main__":
    unittest.main()
