"""地图工具测试: 采用合法选择, 其他情况退回启发式."""

from __future__ import annotations

import unittest

from brain.contracts import DecisionKind, DecisionRequest, Source
from brain.llm import make_mock_client
from brain.model import GameState
from brain.tools.map_router import MapRouterTool, fallback

MAP = {
    "state_type": "map",
    "map": {
        "next_options": [
            {"index": 0, "type": "Monster", "leads_to": [{"type": "Monster"}]},
            {"index": 1, "type": "Elite", "leads_to": [{"type": "Shop"}]},
            {"index": 2, "type": "RestSite", "leads_to": [{"type": "Elite"}]},
        ],
        "boss": {"name": "墨影幻灵"},
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 30, "max_hp": 80, "gold": 40,
               "deck": [{"name": "打击"}, {"name": "防御"}]},
}


def request() -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.MAP, state=GameState.parse(MAP))


class MapToolTests(unittest.TestCase):
    def test_llm_choice_is_used(self):
        tool = MapRouterTool(make_mock_client({"choice": 1, "reason": "血够，拿遗物"}))
        decision = tool.decide(request())
        self.assertEqual(decision.action.option, 1)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.LLM)
        self.assertIn("遗物", decision.reason)

    def test_invalid_choice_falls_back(self):
        tool = MapRouterTool(make_mock_client({"choice": 99, "reason": "瞎选"}))
        decision = tool.decide(request())
        self.assertIs(decision.source, Source.HEURISTIC)
        self.assertEqual(decision.action.option, 2)  # 低血量时选择篝火

    def test_no_client_is_heuristic(self):
        decision = MapRouterTool(None).decide(request())
        self.assertIs(decision.source, Source.HEURISTIC)

    def test_fallback_prefers_rest_when_hurt(self):
        index, _ = fallback(GameState.parse(MAP))
        self.assertEqual(index, 2)

    def test_fallback_prefers_elite_when_healthy(self):
        healthy = {**MAP, "player": {**MAP["player"], "hp": 75}}
        index, _ = fallback(GameState.parse(healthy))
        self.assertEqual(index, 1)


if __name__ == "__main__":
    unittest.main()
