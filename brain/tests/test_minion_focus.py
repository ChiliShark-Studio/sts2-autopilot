"""幻象爪牙与首领: 幻象怪打死会复活, 首领死后爪牙离场."""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.combat.search import _Sim, _plan, decide_state

OBSCURA = {"entity_id": "THE_OBSCURA_0", "name": "胧光怪", "hp": 53, "max_hp": 123,
           "intents": [{"type": "Attack", "damage": 12, "hits": 1, "label": "12",
                        "description": "这个敌人将要攻击造成12点伤害。"}],
           "status": []}

PARAFRIGHT = {"entity_id": "PARAFRIGHT_0", "name": "寄生惧魔", "hp": 21, "max_hp": 21,
              "intents": [{"type": "Attack", "damage": 21, "hits": 1, "label": "21",
                           "description": "这个敌人将要攻击造成21点伤害。"}],
              "status": [{"id": "ILLUSION_POWER", "name": "幻象", "amount": 1,
                          "description": "死亡时，下回合会以完整生命值复活。"},
                         {"id": "MINION_POWER", "name": "爪牙", "amount": 1,
                          "description": "爪牙会在他们的领导者死亡时放弃战斗。"}]}


def state(*, hand, enemies, energy=3, hp=27, incoming_block=0):
    return {
        "state_type": "monster",
        "battle": {"round": 7, "turn": "player", "ready_for_action": True,
                   "enemies": enemies},
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "block": incoming_block,
                   "hand": hand, "potions": []},
    }


def strike(index=0, damage=6):
    return {"index": index, "name": "打击", "cost": "1", "damage": damage, "hits": 1,
            "target_type": "AnyEnemy"}


class ParseTests(unittest.TestCase):
    def test_illusion_and_minion_flags(self):
        parsed = GameState.parse(state(hand=[strike()], enemies=[OBSCURA, PARAFRIGHT]))
        sim = _Sim.build(parsed.player, parsed.battle)
        minion = sim.enemies[1]
        self.assertTrue(minion.revives)
        self.assertTrue(minion.is_minion)
        self.assertFalse(sim.enemies[0].revives)


class TargetingTests(unittest.TestCase):
    def test_prefers_leader_over_reviving_minion(self):
        # 两只都能碰: 打首领的 53 血是真实战果, 打幻象爪牙的血量会被重置.
        decision = decide_state(state(hand=[strike()], enemies=[OBSCURA, PARAFRIGHT]))
        self.assertEqual(decision.action.target, "THE_OBSCURA_0")

    def test_killing_the_minion_is_not_rewarded(self):
        # 血够、爪牙威胁不致命时: 不该为了杀它会复活的爪牙而放弃首领.
        calm_minion = dict(PARAFRIGHT,
                           intents=[{"type": "Attack", "damage": 3, "hits": 1, "label": "3",
                                     "description": "这个敌人将要攻击造成3点伤害。"}])
        decision = decide_state(state(hand=[strike(), strike(1)], enemies=[OBSCURA, calm_minion],
                                      hp=60))
        self.assertEqual(decision.action.target, "THE_OBSCURA_0")

    def test_killing_leader_counts_as_win(self):
        # 首领只剩 5 血: 斩杀首领即结束战斗, 不用管爪牙.
        weak_leader = dict(OBSCURA, hp=5)
        decision = decide_state(state(hand=[strike()], enemies=[weak_leader, PARAFRIGHT]))
        self.assertEqual(decision.action.target, "THE_OBSCURA_0")
        self.assertIn("斩杀", decision.reason)


class ValueTests(unittest.TestCase):
    def test_reviving_minion_hp_does_not_add_score(self):
        parsed = GameState.parse(state(hand=[strike()], enemies=[OBSCURA, PARAFRIGHT]))
        sim = _Sim.build(parsed.player, parsed.battle)
        before = sim.value()
        hurt = sim.clone()
        hurt.enemies[1].hp = 3          # 把幻象爪牙打残
        self.assertAlmostEqual(hurt.value(), before)  # 不涨分

    def test_leader_dead_means_won(self):
        parsed = GameState.parse(state(hand=[strike()],
                                       enemies=[dict(OBSCURA, hp=0), PARAFRIGHT]))
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertTrue(_plan(sim, 10, 7).won)


if __name__ == "__main__":
    unittest.main()
