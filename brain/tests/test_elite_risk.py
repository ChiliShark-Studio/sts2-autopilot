"""精英风险评估: 地图只显示"精英", 所以按本幕本场景所有精英取最坏情况."""

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

# 密林场景 (Boss 墨影幻灵). 场景由 Boss id 反推, 所以这里必须带 id.
OVERGROWTH_BOSS = "VANTOM_BOSS"
UNDERDOCKS_BOSS = "SOUL_FYSH_BOSS"

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
        "boss": {"id": OVERGROWTH_BOSS, "name": "墨影幻灵"},
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
        "boss": {"id": OVERGROWTH_BOSS, "name": "墨影幻灵"},
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 40, "max_hp": 80, "gold": 120,
               "deck": [{"name": "打击"}], "relics": [], "potions": []},
}

STARTER_MAP = dict(ELITE_MAP)


class RosterTests(unittest.TestCase):
    """精英池按场景划分: 第一幕的密林与暗港各 3 只, 不能混算."""

    def test_overgrowth_elites(self):
        self.assertEqual(scenarios.elites_for(1, OVERGROWTH_BOSS),
                         ("bygone_effigy", "byrdonis", "phrog_parasite"))

    def test_underdocks_elites(self):
        self.assertEqual(scenarios.elites_for(1, UNDERDOCKS_BOSS),
                         ("skulking_colony", "phantasmal_gardeners", "terror_eel"))

    def test_every_listed_elite_is_modeled(self):
        for region in scenarios.REGIONS.values():
            for name in region.elites:
                self.assertIn(name, scenarios.ENCOUNTERS, f"{region.name}: {name}")

    def test_unknown_boss_does_not_mix_two_scenes(self):
        # 同幕有多个场景时, 未识别场景宁可返回空, 也不能把另一个场景的精英算进来.
        self.assertEqual(scenarios.elites_for(1, ""), ())
        self.assertEqual(scenarios.elites_for(2, ""), ())


class AssessTests(unittest.TestCase):
    def test_reports_every_elite_of_the_region(self):
        result = assess(deck(), hp=80, max_hp=80, act=1, boss_id=UNDERDOCKS_BOSS, runs=2)
        self.assertEqual(len(result.risks), 3)
        self.assertEqual(result.unmodeled, [])
        self.assertIn("skulking_colony", [r.encounter for r in result.risks])
        self.assertNotIn("bygone_effigy", [r.encounter for r in result.risks])
        # 最坏情况不会比任一单只精英更好.
        self.assertLessEqual(result.worst_win_rate, min(r.win_rate for r in result.risks))
        self.assertGreaterEqual(result.worst_damage, max(r.max_damage for r in result.risks))

    def test_unmodeled_elite_blocks_the_safe_claim(self):
        # 以后新增场景里有没重建的精英时, 不能凭"模拟过的那几只"就判定安全.
        from brain.sim.risk import EliteRisk, EliteAssessment

        result = EliteAssessment([EliteRisk("x", "测试精英", 1.0, 0.0, 0)], unmodeled=["未建模"])
        self.assertFalse(result.safe)
        self.assertFalse(result.affordable(80))

    def test_low_hp_is_not_affordable(self):
        result = assess(deck(), hp=3, max_hp=80, act=1, boss_id=OVERGROWTH_BOSS, runs=2)
        self.assertFalse(result.affordable(3))

    def test_high_hp_is_affordable(self):
        # 足够强的牌组: 80 血打第一幕精英应当稳赢且掉血可控.
        strong = [DeckCard("巨力", cost=1, damage=30)] * 10
        result = assess(strong, hp=80, max_hp=80, act=1, boss_id=OVERGROWTH_BOSS, runs=2)
        self.assertTrue(result.affordable(80))

    def test_margin_is_respected(self):
        result = assess(deck(), hp=80, max_hp=80, act=1, boss_id=OVERGROWTH_BOSS, runs=2)
        exact = int(result.worst_damage)
        self.assertFalse(result.affordable(exact + SAFETY_MARGIN - 1))
        self.assertTrue(result.affordable(exact + SAFETY_MARGIN) or not result.safe)

    def test_description_lists_each_elite(self):
        result = assess(deck(), hp=60, max_hp=80, act=1, boss_id=OVERGROWTH_BOSS, runs=1)
        text = _describe_risk(result)
        for risk in result.risks:
            self.assertIn(risk.name, text)


class RouterTests(unittest.TestCase):
    def test_elite_risk_reads_the_region_from_the_boss(self):
        # 状态里没有场景名, 只能按本幕 Boss 反推; 暗港不能用密林的精英估算.
        tool = MapRouterTool(make_mock_client({}), StrategyContext())
        map_raw = dict(ELITE_MAP["map"])
        map_raw["boss"] = {"id": UNDERDOCKS_BOSS, "name": "灵魂异鱼"}
        result = tool._elite_risk(GameState.parse({**ELITE_MAP, "map": map_raw}))
        self.assertEqual({r.encounter for r in result.risks},
                         {"skulking_colony", "phantasmal_gardeners", "terror_eel"})

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
