"""与我一战！+ 会给被打的目标加力量, 选目标时要避开"会变强"的敌人.

真机第 1 回合: 模型把这张牌打在聚合体身上 (它在攻击), 等于给它加力量。
正确做法是打女王——她这回合不攻击, 加力量影响最小。
"""

from __future__ import annotations

import unittest

from brain.combat.search import _Sim, decide_state
from brain.effects import parse_card
from brain.model import GameState

TRUMP = {"name": "与我一战！+", "cost": "2", "damage": 6, "hits": 2, "strength": 4,
         "target_type": "AnyEnemy", "can_play": True,
         "description": "造成6点伤害两次。 获得4点力量。 该敌人获得1点力量。"}


def state(*, queen_incoming=0):
    return {
        "state_type": "boss",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [
                {"entity_id": "TORCH_HEAD_AMALGAM_0", "name": "火炬头聚合体", "hp": 199,
                 "max_hp": 199, "status": [{"id": "MINION_POWER", "name": "爪牙", "amount": 1}],
                 "intents": [{"type": "Attack", "damage": 18, "hits": 1}]},
                {"entity_id": "QUEEN_0", "name": "女王", "hp": 400, "max_hp": 400,
                 "status": [],
                 "intents": ([{"type": "Attack", "damage": queen_incoming, "hits": 1}]
                             if queen_incoming else [{"type": "Buff"}])},
            ],
        },
        "player": {"hp": 72, "max_hp": 84, "energy": 3, "status": [],
                   "hand": [dict(TRUMP, index=0)], "potions": []},
    }


class TrumpTargetTests(unittest.TestCase):
    def test_side_effect_is_parsed(self):
        parsed = parse_card(dict(TRUMP))
        self.assertEqual(parsed["strength"], 4)
        self.assertEqual(parsed["enemy_strength"], 1)

    def test_side_effect_raises_that_enemy_incoming(self):
        parsed = GameState.parse(state())
        sim = _Sim.build(parsed.player, parsed.battle, hp_value_scale=1.0)
        minion = next(i for i, e in enumerate(sim.enemies) if e.is_minion)
        before = sim.enemies[minion].incoming
        sim.play(0, minion)
        self.assertEqual(sim.enemies[minion].incoming, before + 1)

    def test_it_hits_the_queen_not_the_attacker(self):
        # 聚合体在攻击, 女王这回合不攻击: 加力量给女王损失最小.
        decision = decide_state(state())
        self.assertEqual(decision.action.target, "QUEEN_0")


if __name__ == "__main__":
    unittest.main()
