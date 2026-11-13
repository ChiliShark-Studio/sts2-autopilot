"""精英风险评估: 地图只显示"精英", 所以按本幕所有精英取最坏情况."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from brain.contracts import DecisionKind, DecisionRequest
from brain.llm import make_mock_client
from brain.model import GameState
from brain.sim import scenarios
from brain.sim.engine import DeckCard, deck_from_cards
from brain.sim.risk import SAFETY_MARGIN, assess
from brain.map.router import MapRouterTool, _describe_risk
from brain.strategy.context import StrategyContext

# 起点 (1,1) 只有一条路: 下一跳就是精英.
ELITE_MAP = {
    "state_type": "map",
    "map": {
        "current_position": {"col": 1, "row": 1, "type": "Monster"},
        "next_options": [
            {"index": 0, "col": 1, "row": 2, "type": "Elite"},
        ],
        "nodes": [
            {"col": 1, "row": 1, "type": "Monster", "children": [[1, 2]]},
            {"col": 1, "row": 2, "type": "Elite", "children": [[1, 3]]},
            {"col": 1, "row": 3, "type": "Boss", "children": []},
        ],
        "boss": {"name": "墨影幻灵"},
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80, "gold": 120,
               "deck": [{"name": "打击", "damage": 6}, {"name": "防御", "block": 5}]},
}


def deck():
    from brain.sim.scenarios import starter
    return starter("ironclad")


# 起点 (1,1) 分叉: 精英 (1,2) 或休息点 (2,2).
ELITE_FORK = {
    "state_type": "map",
    "map": {
        "current_position": {"col": 1, "row": 1, "type": "Monster"},
        "next_options": [
            {"index": 0, "col": 1, "row": 2, "type": "Elite"},
            {"index": 1, "col": 2, "row": 2, "type": "RestSite"},
        ],
        "nodes": [
            {"col": 1, "row": 1, "type": "Monster", "children": [[1, 2], [2, 2]]},
            {"col": 1, "row": 2, "type": "Elite", "children": [[1, 3]]},
            {"col": 2, "row": 2, "type": "RestSite", "children": [[1, 3]]},
            {"col": 1, "row": 3, "type": "Boss", "children": []},
        ],
        "boss": {"name": "墨影幻灵"},
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 40, "max_hp": 80, "gold": 120,
               "deck": [{"name": "打击"}], "relics": [], "potions": []},
}

STARTER_MAP = dict(ELITE_MAP)


class RosterTests(unittest.TestCase):
    def test_act_one_has_three_elites(self):
        self.assertEqual(len(scenarios.ELITES_BY_ACT[1]), 3)
        for name in scenarios.ELITES_BY_ACT[1]:
            self.assertIn(name, scenarios.ENCOUNTERS)


class AssessTests(unittest.TestCase):
    def test_reports_every_elite(self):
        result = assess(deck(), hp=80, max_hp=80, act=1, runs=2)
        self.assertEqual(len(result.risks), 3)
        self.assertIn("bygone_effigy", [r.encounter for r in result.risks])
        # 最坏情况不会比任一单只精英更好.
        self.assertLessEqual(result.worst_win_rate, min(r.win_rate for r in result.risks))
        self.assertGreaterEqual(result.worst_damage, max(r.max_damage for r in result.risks))

    def test_low_hp_is_not_affordable(self):
        result = assess(deck(), hp=3, max_hp=80, act=1, runs=2)
        self.assertFalse(result.affordable(3))

    def test_high_hp_is_affordable(self):
        # 足够强的牌组: 80 血打第一幕精英应当稳赢且掉血可控.
        strong = [DeckCard("巨力", cost=1, damage=30)] * 10
        result = assess(strong, hp=80, max_hp=80, act=1, runs=2)
        self.assertTrue(result.affordable(80))

    def test_margin_is_respected(self):
        result = assess(deck(), hp=80, max_hp=80, act=1, runs=2)
        exact = int(result.worst_damage)
        self.assertFalse(result.affordable(exact + SAFETY_MARGIN - 1))
        self.assertTrue(result.affordable(exact + SAFETY_MARGIN) or not result.safe)

    def test_description_lists_each_elite(self):
        result = assess(deck(), hp=60, max_hp=80, act=1, runs=1)
        text = _describe_risk(result)
        for risk in result.risks:
            self.assertIn(risk.name, text)


class RouterTests(unittest.TestCase):
    def test_unaffordable_elite_triggers_replan(self):
        client = make_mock_client({})
        client.json_chat = Mock(return_value={"route": [[2, 2]], "reason": "打不过，绕开精英"})
        context = StrategyContext(route=((1, 2),), route_note="开局计划")
        tool = MapRouterTool(client, context)
        tool._elite_risk = Mock(return_value=_unaffordable())
        state = GameState.parse(ELITE_FORK)
        decision = tool.decide(DecisionRequest(kind=DecisionKind.MAP, state=state, scene="map"))
        client.json_chat.assert_called()
        # 绕开精英, 改走休息点.
        self.assertEqual(decision.action.to_payload()["index"], 1)

    def test_affordable_elite_follows_plan(self):
        client = make_mock_client({})
        client.json_chat = Mock(return_value={"route": [], "reason": "不该被调用"})
        context = StrategyContext(route=((1, 2),), route_note="开局计划")
        tool = MapRouterTool(client, context)
        tool._elite_risk = Mock(return_value=_affordable())
        state = GameState.parse(ELITE_FORK)
        decision = tool.decide(DecisionRequest(kind=DecisionKind.MAP, state=state, scene="map"))
        self.assertEqual(decision.source.value, "script")
        self.assertEqual(decision.action.to_payload()["index"], 0)
        client.json_chat.assert_not_called()


def _affordable():
    from brain.sim.risk import EliteRisk, EliteAssessment

    return EliteAssessment([EliteRisk("bygone_effigy", "旧日雕像", 1.0, 10.0, 12)])


def _unaffordable():
    from brain.sim.risk import EliteRisk, EliteAssessment

    return EliteAssessment([EliteRisk("bygone_effigy", "旧日雕像", 0.0, 70.0, 72)])


if __name__ == "__main__":
    unittest.main()
