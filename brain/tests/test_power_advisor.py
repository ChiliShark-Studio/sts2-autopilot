"""未建模的能力牌交给模型判断是否打出.

本地战斗模型只认得力量/敏捷/覆甲/残酷等少数能力; 其余能力牌在模拟里等于白板,
打不打完全取决于实际效果。没有模型时保持原行为: 不打, 不拿能量赌一张算不出价值的牌。
"""

from __future__ import annotations

import unittest

from brain.combat.search import CombatSearchTool, unmodeled_power
from brain.combat.tempo_advisor import PowerAdvisor
from brain.contracts import DecisionKind, DecisionRequest, Source
from brain.llm import make_mock_client
from brain.model import GameState

# 只写了效果文本、模型里没有对应数值的能力牌.
UNKNOWN_POWER = {
    "index": 0, "id": "TEST_POWER", "name": "未知能力", "type": "Power", "cost": "1",
    "target_type": "Self", "can_play": True, "damage": 0, "block": 0, "hits": 1,
    "description": "每当你打出一张攻击牌, 抽一张牌。",
}
STONE = {
    "index": 1, "id": "STONE_ARMOR", "name": "岩石铠甲", "type": "Power", "cost": "1",
    "target_type": "Self", "can_play": True, "damage": 0, "block": 0, "hits": 1,
    "description": "获得4层覆甲。",
}
STRIKE = {"index": 2, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}


def state(hand=None, hp=60, energy=3):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "怪", "hp": 60, "max_hp": 60,
                         "status": [], "intents": [{"type": "Attack", "damage": 10, "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "status": [],
                   "hand": hand if hand is not None else [dict(UNKNOWN_POWER, index=0),
                                                          dict(STRIKE, index=1)]},
    }


def request(raw):
    return DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw))


class UnmodeledPowerTests(unittest.TestCase):
    def test_detection(self):
        cards = {c.name: c for c in GameState.parse(state()).battle.hand}
        self.assertTrue(unmodeled_power(cards["未知能力"]))
        self.assertFalse(unmodeled_power(cards["打击"]))

    def test_model_can_tell_us_to_play_it(self):
        client = make_mock_client({})
        client.json_chat = lambda system, user: {"choice": 0, "reason": "每回合多抽牌, 越早越好"}
        tool = CombatSearchTool(power=PowerAdvisor(client))
        decision = tool.decide(request(state()))
        self.assertIs(decision.source, Source.LLM)
        self.assertEqual(decision.action.card.name, "未知能力")

    def test_model_can_tell_us_to_keep_it(self):
        client = make_mock_client({})
        client.json_chat = lambda system, user: {"choice": -1, "reason": "先解场"}
        tool = CombatSearchTool(power=PowerAdvisor(client))
        decision = tool.decide(request(state()))
        self.assertNotEqual(getattr(getattr(decision.action, "card", None), "name", ""),
                            "未知能力")

    def test_without_a_model_it_is_not_played(self):
        tool = CombatSearchTool(power=PowerAdvisor(None))
        decision = tool.decide(request(state()))
        self.assertNotEqual(getattr(getattr(decision.action, "card", None), "name", ""),
                            "未知能力")

    def test_modelled_power_is_not_asked(self):
        calls = []
        client = make_mock_client({})
        client.json_chat = lambda system, user: calls.append(user) or {"choice": 0}
        tool = CombatSearchTool(power=PowerAdvisor(client))
        tool.decide(request(state(hand=[dict(STONE, index=0), dict(STRIKE, index=1)])))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
