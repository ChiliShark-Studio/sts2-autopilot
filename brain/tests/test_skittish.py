"""胆小 (SKITTISH_POWER) 建模测试.

真机证据 (2026-11-14 暗港精英战, 花园幽灵鳗 31/31 带 ``SKITTISH_POWER 胆小 6``):

    step 463  hp 26  block 0
    step 474  打出御血术 15 点 -> hp 11  block 0   # 第一下全额命中
    step 476  hp 11  block 6                     # 命中之后才获得格挡
    step 482  block 3                            # 这 6 点挡掉后续命中
    step 490  敌人回合开始 -> block 0

因此正确的结算顺序是: 先结算这一下的伤害, 再给它加 6 点格挡, 用来保护同一回合的
后续命中; 回合结束后清空. 反过来写会让第一下白吃 6 点, 是我们先前犯过的错.
"""

from __future__ import annotations

import unittest

from brain.combat.search import _Sim
from brain.model import GameState
from brain.sim import scenarios
from brain.sim.engine import DeckCard, Fight, Intent, Monster


def sim(*, hp=31, skittish=6, damage=10, hits=1, hand=None):
    status = [{"id": "SKITTISH_POWER", "name": "胆小", "amount": skittish}] if skittish else []
    raw = {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "PHANTASMAL_GARDENER_0", "name": "花园幽灵鳗",
                         "hp": hp, "max_hp": 31, "status": status,
                         "intents": [{"type": "Attack", "damage": 5, "hits": 1}]}],
        },
        "player": {"hp": 60, "max_hp": 80, "energy": 3,
                   "hand": hand or [{"index": 0, "name": "重击", "cost": "1",
                                     "damage": damage, "hits": hits,
                                     "target_type": "AnyEnemy"}]},
    }
    state = GameState.parse(raw)
    return _Sim.build(state.player, state.battle)


class SearchSkittishTests(unittest.TestCase):
    def test_first_hit_lands_in_full_then_grants_block(self):
        s = sim(damage=10)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 31 - 10)   # 第一下不减免
        self.assertEqual(s.enemies[0].block, 6)      # 命中之后才加格挡

    def test_second_hit_in_the_same_turn_is_absorbed(self):
        s = sim(damage=10, hand=[
            {"index": 0, "name": "重击", "cost": "1", "damage": 10, "hits": 1, "target_type": "AnyEnemy"},
            {"index": 1, "name": "重击", "cost": "1", "damage": 10, "hits": 1, "target_type": "AnyEnemy"},
        ])
        s.play(0, 0)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 31 - 10 - (10 - 6))

    def test_multi_hit_only_protects_after_the_first_hit(self):
        # 1 点 3 段: 第一段扣 1 血, 之后拿到 6 点格挡, 剩下两段被吃掉.
        s = sim(damage=1, hits=3)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 30)
        self.assertEqual(s.enemies[0].block, 4)

    def test_enemy_without_the_power_is_unaffected(self):
        s = sim(skittish=0, damage=10)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 31 - 10)


class EngineSkittishTests(unittest.TestCase):
    def test_engine_order_and_turn_reset(self):
        monster = Monster("花园幽灵鳗", hp=31, max_hp=31, script=[Intent("attack", 0)],
                          skittish=6)
        fight = Fight([DeckCard("重击", cost=0, damage=10)] * 4, [monster], seed=1)
        fight.start_turn()
        fight.play(0)
        self.assertEqual((monster.hp, monster.block), (21, 6))    # 第一下全额, 随后加格挡
        fight.play(0)
        self.assertEqual((monster.hp, monster.block), (17, 0))    # 第二下被 6 点格挡吸收 6
        fight.end_turn()
        fight.start_turn()
        fight.play(0)
        self.assertEqual((monster.hp, monster.block), (7, 6))     # 新回合重新触发

    def test_scenario_has_four_skittish_gardeners(self):
        gardeners = scenarios.encounter("phantasmal_gardeners")
        self.assertEqual(len(gardeners), 4)
        for monster in gardeners:
            self.assertEqual((monster.hp, monster.max_hp, monster.skittish), (31, 31, 6))
            self.assertEqual(monster.kind, "elite")


if __name__ == "__main__":
    unittest.main()
