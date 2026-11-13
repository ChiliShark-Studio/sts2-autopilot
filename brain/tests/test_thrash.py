"""痛殴 (THRASH): 4 点伤害两段, 并烧掉手牌里一张随机攻击牌把它的伤害加给自己.

注意时机: 吸收来的伤害是加到"这张牌"上的, **下一次打出**才生效,
当回合仍然是 4 点两段。第一次实现时把它算进了当回合伤害, 是错的。
"""

from __future__ import annotations

import unittest

from brain.combat.search import _Sim, decide_state
from brain.effects import parse_card
from brain.model import GameState

THRASH_RAW = {
    "id": "THRASH", "name": "痛殴", "type": "Attack", "cost": "1", "target_type": "AnyEnemy",
    "damage": 4, "hits": 1, "can_play": True,
    "description": "造成4点伤害两次。 消耗你的手牌中随机一张攻击牌，并将它的伤害添加给这张牌。",
}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1, "target_type": "AnyEnemy",
          "can_play": True}


def state(hand=None, energy=3, enemy_hp=45):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "怪", "hp": enemy_hp, "max_hp": enemy_hp,
                         "status": [], "intents": [{"type": "Attack", "damage": 12, "hits": 1}]}],
        },
        "player": {"hp": 70, "max_hp": 80, "energy": energy, "status": [],
                   "hand": hand if hand is not None else [
                       dict(THRASH_RAW, index=0), dict(STRIKE, index=1)]},
    }


class ThrashTests(unittest.TestCase):
    def test_two_hits_and_absorb_are_parsed(self):
        parsed = parse_card(dict(THRASH_RAW))
        self.assertEqual(parsed["hits"], 2)
        self.assertTrue(parsed["absorb_attack"])

    def test_this_play_is_only_eight_damage(self):
        # 4 × 2 = 8, 吸收来的伤害不在这一下生效.
        parsed = GameState.parse(state())
        sim = _Sim.build(parsed.player, parsed.battle, hp_value_scale=1.0)
        position = next(i for i, c in enumerate(sim.hand) if c.name == "痛殴")
        sim.play(position, 0)
        self.assertEqual(sim.enemies[0].hp, 45 - 8)

    def test_absorbed_damage_is_booked_as_future_value(self):
        parsed = GameState.parse(state())
        sim = _Sim.build(parsed.player, parsed.battle, hp_value_scale=1.0)
        position = next(i for i, c in enumerate(sim.hand) if c.name == "痛殴")
        before = sim.future_value
        sim.play(position, 0)
        self.assertGreater(sim.future_value, before)

    def test_it_is_still_preferred_over_a_single_strike(self):
        decision = decide_state(state())
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "痛殴")


if __name__ == "__main__":
    unittest.main()
