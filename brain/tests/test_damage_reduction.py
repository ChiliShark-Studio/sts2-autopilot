"""Damage-reduction powers: the floor-17 boss review found these missing.

滑溜 (SLIPPERY_POWER): the next N times the enemy would lose HP, it loses 1.
无实体 (INTANGIBLE_POWER): every hit is capped at 1.

Both cap **per hit**, so multi-hit attacks burn 滑溜 stacks faster -- the sim
must model that, otherwise every "I deal 29 this turn" estimate is fiction.
"""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.tools.combat_search import _Sim


def sim(*, enemy_hp=20, powers=None, hand=None, hits=1, damage=6):
    raw = {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "滑溜怪", "hp": enemy_hp, "max_hp": enemy_hp,
                         "status": powers or [],
                         "intents": [{"type": "Attack", "damage": 5, "hits": 1}]}],
        },
        "player": {"hp": 70, "max_hp": 70, "energy": 3,
                   "hand": hand or [{"index": 0, "name": "打击", "cost": "1",
                                     "damage": damage, "hits": hits, "target_type": "AnyEnemy"}]},
    }
    state = GameState.parse(raw)
    return _Sim.build(state.player, state.battle)


class SlipperyTests(unittest.TestCase):
    def test_single_hit_is_capped_and_consumes_a_stack(self):
        s = sim(enemy_hp=20, powers=[{"id": "SLIPPERY_POWER", "name": "滑溜", "amount": 8}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 19)   # 6 damage -> 1
        self.assertEqual(s.enemies[0].slippery, 7)

    def test_multi_hit_burns_stacks_then_deals_full_damage(self):
        # 3 hits of 3 damage vs 2 stacks: 1 + 1 + 3 = 5.
        s = sim(enemy_hp=20, damage=3, hits=3,
                powers=[{"id": "SLIPPERY_POWER", "name": "滑溜", "amount": 2}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 15)
        self.assertEqual(s.enemies[0].slippery, 0)

    def test_name_only_status_still_works(self):
        """Older logs carry localised names without ids."""
        s = sim(enemy_hp=20, powers=[{"name": "滑溜", "amount": 4}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 19)

    def test_reduction_flag_exposed(self):
        s = sim(powers=[{"id": "SLIPPERY_POWER", "amount": 3}])
        self.assertTrue(s.has_damage_reduction)
        s2 = sim()
        self.assertFalse(s2.has_damage_reduction)


class IntangibleTests(unittest.TestCase):
    def test_every_hit_is_one(self):
        s = sim(enemy_hp=20, damage=3, hits=3,
                powers=[{"id": "INTANGIBLE_POWER", "name": "无实体", "amount": 1}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 17)  # 3 hits x 1


if __name__ == "__main__":
    unittest.main()
