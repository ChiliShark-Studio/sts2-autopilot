"""残酷 (CRUELTY): 易伤敌人额外受伤的稀有 power.

真机证据 (2026-11-14 第 13 层): 牌组拿到了残酷, 但整局一次都没打出——
搜索只按当回合算账, 敌人当时没有易伤, 这张 1 费能力牌在模型里就是白板.
现在把它折算成"后续回合的易伤攻击收益".
"""

from __future__ import annotations

import unittest

from brain.combat.search import _Sim, decide_state
from brain.model import GameState

CRUELTY = {"index": 0, "id": "CRUELTY", "name": "残酷", "type": "Power", "cost": "1",
           "target_type": "Self", "can_play": True, "damage": 0, "block": 0, "hits": 1,
           "description": "有易伤状态的敌人额外受到25%的伤害。"}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1, "target_type": "AnyEnemy",
          "can_play": True}


def state(*, vulnerable=0, enemy_hp=60, energy=3, hand=None):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "怪", "hp": enemy_hp, "max_hp": enemy_hp,
                         "status": ([{"id": "VULNERABLE_POWER", "name": "易伤",
                                      "amount": vulnerable}] if vulnerable else []),
                         "intents": [{"type": "Attack", "damage": 8, "hits": 1}]}],
        },
        "player": {"hp": 60, "max_hp": 80, "energy": energy, "status": [],
                   "hand": hand if hand is not None else [dict(CRUELTY, index=0),
                                                          dict(STRIKE, index=1)]},
    }


class CrueltyTests(unittest.TestCase):
    def test_description_is_parsed(self):
        card = GameState.parse(state()).battle.hand[0]
        self.assertAlmostEqual(card.cruelty, 0.25)

    def test_vulnerable_damage_grows_by_a_quarter(self):
        plain = GameState.parse(state(vulnerable=1))
        boosted = GameState.parse(state(vulnerable=1))
        sim = _Sim.build(plain.player, plain.battle, hp_value_scale=1.0)
        sim.cruelty = 0.25                       # 已打出残酷
        base = _Sim.build(boosted.player, boosted.battle, hp_value_scale=1.0)
        for node in (sim, base):
            strike = next(i for i, c in enumerate(node.hand) if c.name == "打击")
            node.play(strike, 0)
        # 6 → 易伤 9 → 再多 25% 是 10.
        self.assertEqual(base.enemies[0].hp, 60 - int(6 * 1.5))
        self.assertEqual(sim.enemies[0].hp, 60 - int(6 * 1.75))

    def test_it_is_played_even_when_no_enemy_is_vulnerable_yet(self):
        decision = decide_state(state(vulnerable=0))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "残酷")

    def test_not_played_when_it_cannot_afford(self):
        decision = decide_state(state(energy=0))
        # 0 能量时打不出, 应当是结束回合或其他动作, 而不是硬打残酷.
        self.assertNotEqual(getattr(decision.action, "card", None), "残酷")
        self.assertEqual(decision.action.kind, "end_turn")


if __name__ == "__main__":
    unittest.main()
