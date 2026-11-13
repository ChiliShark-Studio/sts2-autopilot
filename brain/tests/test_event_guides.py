"""事件与先古遗物数据表: 按当前事件注入, 不写死在提示词里."""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.strategy.ancients import ANCIENTS, guide_for as ancient_guide
from brain.strategy.events import EVENTS, guide_for as event_guide
from brain.strategy.situation import situation
from brain.strategy.context import StrategyContext


def event_state(name: str, options=None):
    event = {"event_id": "TEST", "event_name": name, "is_ancient": False,
             "options": options or []}
    return GameState.parse({"state_type": "event", "event": event,
                            "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80,
                                       "deck": []}})


class EventGuideTests(unittest.TestCase):
    def test_brain_leech_ranks_ripping_first(self):
        guide = event_guide("脑蛭")
        self.assertIsNotNone(guide)
        self.assertEqual(guide["options"][0]["choice"], "把它扯下来")
        self.assertGreater(guide["options"][0]["lift"], 0)

    def test_self_help_book_flags_the_caveat(self):
        guide = event_guide("自助指南")
        self.assertIsNotNone(guide)
        self.assertIn("按附魔的实际效果判断", guide["note"])

    def test_unknown_event_returns_none(self):
        self.assertIsNone(event_guide("不存在的事件"))

    def test_every_guide_has_ranked_options(self):
        for guide in EVENTS:
            lifts = [lift for _, lift, _ in guide.options]
            self.assertEqual(lifts, sorted(lifts, reverse=True), guide.name)


class AncientGuideTests(unittest.TestCase):
    def test_tezcatara_marks_toasty_mittens_as_trap(self):
        guide = ancient_guide("特兹卡塔拉")
        self.assertIsNotNone(guide)
        tiers = {item["tier"]: item["relics"] for item in guide["tiers"]}
        self.assertTrue(any("烘焙手套" in name for name in tiers["D"]))
        self.assertIn("自毁牌组", guide["note"])

    def test_pael_flesh_is_a_tier(self):
        guide = ancient_guide("佩尔")
        tiers = {item["tier"]: item["relics"] for item in guide["tiers"]}
        self.assertTrue(any("佩尔之肉" in name for name in tiers["A"]))

    def test_all_eight_ancients_are_covered(self):
        self.assertEqual(len(ANCIENTS), 8)

    def test_unknown_ancient_returns_none(self):
        self.assertIsNone(ancient_guide("没有这个先古"))


class SituationInjectionTests(unittest.TestCase):
    def test_event_guide_is_injected_for_a_known_event(self):
        payload = situation(event_state("脑蛭"), StrategyContext())
        self.assertEqual(payload["event_guide"]["event"]["event"], "脑蛭")

    def test_ancient_guide_is_injected_for_an_ancient(self):
        payload = situation(event_state("特兹卡塔拉"), StrategyContext())
        self.assertEqual(payload["event_guide"]["ancient"]["ancient"], "特兹卡塔拉")

    def test_unknown_event_injects_nothing(self):
        payload = situation(event_state("未知事件"), StrategyContext())
        self.assertEqual(payload["event_guide"], {})


if __name__ == "__main__":
    unittest.main()
