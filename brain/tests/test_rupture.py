"""撕裂 (Rupture): 因牌失去生命值时获得力量, 不是打出时立即加.

真机 A1 第一幕拿到的撕裂, 解析成 strength=1 (比燃烧还弱), 完全没体现它和
放血等自伤牌的联动。修复后: 打出只记层数, 之后每次因牌掉血才加力量。
"""

from __future__ import annotations

import unittest

from brain.combat.search import _Sim, decide_state
from brain.effects import parse_card
from brain.model import GameState

RUPTURE = {"name": "撕裂", "cost": "1", "type": "Power", "can_play": True,
           "target_type": "Self", "damage": 0, "block": 0, "strength": 1,
           "description": "每当你从一张牌中失去生命值时，获得1点力量。"}
BLOODLET = {"name": "放血", "cost": "0", "type": "Skill", "can_play": True,
            "target_type": "Self", "damage": 0, "block": 0, "hp_cost": 3, "energy": 2,
            "description": "失去3点生命值。获得2点能量。"}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}


def state(*, hand=None, energy=3):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "怪", "hp": 60, "max_hp": 60,
                         "status": [], "intents": [{"type": "Attack", "damage": 10, "hits": 1}]}],
        },
        "player": {"hp": 60, "max_hp": 80, "energy": energy, "status": [],
                   "hand": hand if hand is not None else [dict(RUPTURE, index=0),
                                                          dict(BLOODLET, index=1)],
                   "deck": [dict(BLOODLET)] * 4 + [dict(STRIKE)] * 6},
    }


class RuptureParseTests(unittest.TestCase):
    def test_it_is_not_an_immediate_strength_card(self):
        parsed = parse_card(dict(RUPTURE))
        self.assertEqual(parsed["strength"], 0)
        self.assertEqual(parsed["rupture"], 1)

    def test_normal_strength_card_is_unaffected(self):
        parsed = parse_card({"name": "燃烧", "cost": "1", "type": "Power",
                             "description": "获得2点力量。"})
        self.assertEqual(parsed["strength"], 2)
        self.assertEqual(parsed["rupture"], 0)


class RuptureSimTests(unittest.TestCase):
    def test_playing_it_grants_no_immediate_strength(self):
        parsed = GameState.parse(state())
        sim = _Sim.build(parsed.player, parsed.battle, hp_value_scale=1.0)
        before = sim.strength
        sim.play(0, None)
        self.assertEqual(sim.strength, before)
        self.assertEqual(sim.rupture, 1)

    def test_losing_hp_from_a_card_grants_strength(self):
        parsed = GameState.parse(state())
        sim = _Sim.build(parsed.player, parsed.battle, hp_value_scale=1.0)
        sim.play(0, None)          # 撕裂
        sim.play(0, None)          # 放血 (掉 3 血)
        self.assertEqual(sim.strength, 1)

    def test_it_is_valued_as_an_engine_when_picking(self):
        decision = decide_state(state(hand=[dict(RUPTURE, index=0), dict(STRIKE, index=1)],
                                      energy=3))
        self.assertIsNotNone(decision.action)


if __name__ == "__main__":
    unittest.main()
