"""模型回复无效时的重试: 必须带上失败原因, 否则会重复同一个答案."""

from __future__ import annotations

import json
import unittest

from brain.contracts import DecisionKind, DecisionRequest
from brain.llm import make_mock_client
from brain.model import GameState
from brain.actions import Proceed
from brain.strategy.context import StrategyContext
from brain.strategy.choice import choose


def _state() -> GameState:
    return GameState.parse({
        "state_type": "rewards",
        "run": {"act": 1, "floor": 5, "ascension": 10},
        "player": {"character": "铁甲战士", "hp": 50, "max_hp": 80, "gold": 99,
                   "deck": [{"name": "打击"}]},
        "rewards": {"items": [{"index": 0, "type": "gold", "description": "30金币"}],
                    "can_proceed": True},
    })


class RetryTests(unittest.TestCase):
    def test_invalid_choice_gets_feedback_then_succeeds(self):
        client = make_mock_client({})
        replies = [{"choice": 13}, {"choice": -1, "reason": "离开"}]
        seen_prompts: list[str] = []

        def fake(system, body):
            seen_prompts.append(body)
            return replies.pop(0)

        client.json_chat = fake
        candidates = [(0, Proceed(), {}), (-1, Proceed(), {})]
        decision = choose(client, _state(), StrategyContext(), task="测试",
                          candidates=candidates, tool="test", attempts=3)
        self.assertIsNotNone(decision)
        self.assertEqual(len(seen_prompts), 2)
        # 第二次请求必须点明上一次的无效原因.
        self.assertIn("不在候选中", seen_prompts[1])
        self.assertIn("13", seen_prompts[1])

    def test_all_invalid_returns_none(self):
        client = make_mock_client({})
        client.json_chat = lambda system, body: {"choice": 99}
        candidates = [(0, Proceed(), {})]
        decision = choose(client, _state(), StrategyContext(), task="测试",
                          candidates=candidates, tool="test", attempts=2)
        self.assertIsNone(decision)

    def test_non_integer_choice_gets_feedback(self):
        client = make_mock_client({})
        seen: list[str] = []

        def fake(system, body):
            seen.append(body)
            return {"choice": "0"}

        client.json_chat = fake
        decision = choose(client, _state(), StrategyContext(), task="测试",
                          candidates=[(0, Proceed(), {})], tool="test", attempts=2)
        self.assertIsNone(decision)
        self.assertIn("JSON", seen[1])



class ShopUnavailableTests(unittest.TestCase):
    """售罄的删牌服务不在候选里, 但必须告诉模型原因."""

    def _shop_state(self):
        return GameState.parse({
            "state_type": "shop",
            "run": {"act": 1, "floor": 6, "ascension": 10},
            "player": {"character": "铁甲战士", "hp": 45, "max_hp": 80, "gold": 246,
                       "deck": [{"name": "打击"}], "relics": [], "potions": [],
                       "max_potion_slots": 3},
            "shop": {"can_proceed": True, "items": [
                {"index": 0, "category": "card", "price": 78, "is_stocked": True,
                 "can_afford": True, "card_name": "上勾拳"},
                {"index": 13, "category": "card_removal", "price": 75,
                 "is_stocked": False, "can_afford": True},
            ]},
        })

    def test_sold_out_item_is_reported_with_reason(self):
        from brain.contracts import DecisionKind
        from brain.build.flow import BuildFlowTool

        client = make_mock_client({})
        payloads: list[dict] = []

        def fake(system, body):
            payloads.append(json.loads(body))
            return {"choice": 13} if len(payloads) == 1 else {"choice": -1, "reason": "售罄就离开"}

        client.json_chat = fake
        context = StrategyContext()
        tool = BuildFlowTool(client, context)
        decision = tool.decide(DecisionRequest(kind=DecisionKind.BUILD,
                                              state=self._shop_state(), scene="shop"))
        self.assertEqual(decision.action.to_payload(), {"action": "proceed"})
        notes = payloads[0]["notes"]["shop_unavailable"]
        self.assertEqual(notes[0]["index"], 13)
        self.assertEqual(notes[0]["reason"], "已售罄")
        # 第二次请求仍然带着说明, 并指出上一次的选择无效.
        self.assertEqual(payloads[1]["notes"]["shop_unavailable"][0]["index"], 13)
        self.assertIn("不在候选中", payloads[1]["previous_reply_invalid"])


if __name__ == "__main__":
    unittest.main()
