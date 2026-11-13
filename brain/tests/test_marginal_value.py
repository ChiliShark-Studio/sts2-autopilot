"""选牌的边际价值: 第二张同名卡不该和第一张同分.

真机复盘: 第 21 层拿了第二张飞剑回旋镖、第 25 层拿了第二张薪火之源——
单卡绝对分把重复占位评得和第一张一样好, 结果牌组变厚、关键牌更少。
"""

from __future__ import annotations

import unittest

from brain.build.scoring import card_score, deck_context, marginal_value
from brain.model import Card, GameState

STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1, "type": "Attack",
          "can_play": True, "description": "造成6点伤害。"}
BOOLET = {"name": "飞剑回旋镖", "cost": "1", "damage": 3, "hits": 3, "type": "Attack",
          "can_play": True, "description": "随机对敌人造成3点伤害3次。"}
PYRE = {"name": "薪火之源", "cost": "2", "type": "Power", "can_play": True,
        "description": "在回合开始时，获得[ironclad_energy_icon.png]。"}
SHRUG = {"name": "耸肩无视", "cost": "1", "block": 8, "type": "Skill", "can_play": True,
         "description": "获得8点格挡。抽1张牌。"}


def card(raw) -> Card:
    state = {"state_type": "monster",
             "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                        "enemies": [{"entity_id": "E0", "name": "怪", "hp": 40, "max_hp": 40,
                                     "status": [], "intents": []}]},
             "player": {"hp": 60, "max_hp": 80, "energy": 3, "status": [],
                        "deck": [dict(raw, index=0)]}}
    return GameState.parse(state).player.deck[0]


class DeckContextTests(unittest.TestCase):
    def test_counts_blocks_and_engines(self):
        deck = [card(SHRUG), card(SHRUG), card(PYRE)]
        context = deck_context(deck)
        self.assertEqual(context["blocks"], 2)
        self.assertEqual(context["engines"], 1)
        self.assertEqual(context["counts"]["耸肩无视"], 2)


class MarginalValueTests(unittest.TestCase):
    def test_second_copy_is_worth_less(self):
        target = card(BOOLET)
        first = marginal_value(target, [card(STRIKE)])
        second = marginal_value(target, [card(STRIKE), card(BOOLET)])
        self.assertLess(second["score"], first["score"])
        self.assertIn("已有 1 张同名", second["notes"][0])

    def test_third_engine_is_penalised(self):
        deck = [card(PYRE), card(PYRE)]
        value = marginal_value(card(PYRE), deck)
        joined = " ".join(value["notes"])
        self.assertIn("引擎收益递减", joined)

    def test_draw_is_valued_when_deck_has_none(self):
        value = marginal_value(card(SHRUG), [card(STRIKE)])
        self.assertTrue(any("过牌" in note for note in value["notes"]))

    def test_first_engine_gets_a_bonus(self):
        value = marginal_value(card(PYRE), [card(STRIKE)])
        self.assertTrue(any("没有引擎" in note for note in value["notes"]))

    def test_base_score_still_exposed(self):
        # 边际分只用于选牌, 单卡分仍要保留给弃牌/商店使用.
        self.assertGreater(card_score(card(BOOLET)), 0)


if __name__ == "__main__":
    unittest.main()
