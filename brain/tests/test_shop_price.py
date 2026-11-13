"""删牌价格随次数上涨: 路线与商店决策都要按实际价格算."""

from __future__ import annotations

import json
import unittest

from brain.contracts import DecisionKind, DecisionRequest
from brain.model import GameState
from brain.build.flow import BuildFlowTool
from brain.strategy.context import StrategyContext
from brain.strategy.situation import removal_price, situation

SHOP = {
    "state_type": "shop", "run": {"act": 1, "floor": 5, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 45, "max_hp": 80, "gold": 120,
               "deck": [{"index": 0, "name": "打击"}, {"index": 1, "name": "防御"}]},
    "shop": {"can_proceed": True, "items": [
        {"index": 0, "category": "card_removal", "price": 100,
         "is_stocked": True, "can_afford": True},
        {"index": 1, "category": "card", "price": 70, "is_stocked": True,
         "can_afford": True, "card_name": "测试牌", "card_description": "造成12点伤害。"},
        {"index": 2, "category": "relic", "price": 90, "is_stocked": True,
         "can_afford": True, "relic_id": "TEST", "relic_description": "测试遗物"},
    ]},
}


class PriceRuleTests(unittest.TestCase):
    def test_a0_progression(self):
        self.assertEqual([removal_price(0, n) for n in range(4)], [75, 100, 125, 150])

    def test_a6_inflation(self):
        self.assertEqual([removal_price(6, n) for n in range(3)], [100, 150, 200])


class SituationTests(unittest.TestCase):
    def test_situation_reports_next_removal_price(self):
        state = GameState.parse(SHOP)
        context = StrategyContext(removals=2)
        data = situation(state, context)
        self.assertEqual(data["shop"]["removals_bought"], 2)
        self.assertEqual(data["shop"]["next_removal_price"], 125)

    def test_ascension_changes_estimate(self):
        state = GameState.parse({**SHOP, "run": {"act": 1, "floor": 5, "ascension": 6}})
        data = situation(state, StrategyContext())
        self.assertEqual(data["shop"]["next_removal_price"], 100)


class CounterTests(unittest.TestCase):
    def test_buying_removal_increments_counter(self):
        from unittest.mock import Mock

        from brain.llm import make_mock_client

        client = make_mock_client({})
        client.json_chat = Mock(return_value={"choice": 0, "reason": "删牌"})
        context = StrategyContext()
        tool = BuildFlowTool(client, context)
        decision = tool.decide(DecisionRequest(kind=DecisionKind.BUILD,
                                              state=GameState.parse(SHOP), scene="shop"))
        self.assertEqual(decision.action.to_payload(), {"action": "shop_purchase", "index": 0})
        self.assertEqual(context.removals, 1)

    def test_buying_a_card_does_not_increment(self):
        from unittest.mock import Mock

        from brain.llm import make_mock_client

        client = make_mock_client({})
        client.json_chat = Mock(return_value={"choice": 1, "reason": "买牌"})
        context = StrategyContext()
        tool = BuildFlowTool(client, context)
        tool.decide(DecisionRequest(kind=DecisionKind.BUILD,
                                    state=GameState.parse(SHOP), scene="shop"))
        self.assertEqual(context.removals, 0)


if __name__ == "__main__":
    unittest.main()
