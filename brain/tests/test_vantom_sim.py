"""墨影幻灵的模拟战斗测试: 滑溜、肢解塞伤口、以及整场可复现.

数值来自 Spire Codex (173 血 / 滑溜 8 / 墨迹 7 / 墨水长枪 6x2 / 肢解 26 + 伤口 / 准备 +2 力量),
属于近似重建, 只用于同种子下的策略对比.
"""

from __future__ import annotations

import unittest

from brain.sim import arena, scenarios
from brain.sim.engine import DeckCard, Fight, Intent, Monster


def _vantom() -> Monster:
    return scenarios.encounter("vantom")[0]


class SlipperyTests(unittest.TestCase):
    def test_each_hit_only_takes_one_hp(self):
        fight = Fight([DeckCard("重击", cost=1, damage=26)],
                      [_vantom()], seed=1)
        fight.start_turn()
        boss = fight.monsters[0]
        before = boss.hp
        self.assertTrue(fight.play(0))
        self.assertEqual(boss.hp, before - 1)   # 26 点伤害只掉 1
        self.assertEqual(boss.slippery, 7)      # 消耗一层

    def test_multi_hit_strips_layers_faster(self):
        fight = Fight([DeckCard("回旋", cost=1, damage=3, hits=3)],
                      [_vantom()], seed=2)
        fight.start_turn()
        boss = fight.monsters[0]
        before = boss.hp
        self.assertTrue(fight.play(0))
        self.assertEqual(boss.hp, before - 3)   # 三段各掉 1
        self.assertEqual(boss.slippery, 5)

    def test_slippery_runs_out_and_damage_goes_through(self):
        fight = Fight([DeckCard("重击", cost=0, damage=20)] * 10, [_vantom()],
                      seed=3, max_energy=10, draw_per_turn=10)
        fight.start_turn()
        boss = fight.monsters[0]
        for _ in range(8):
            self.assertTrue(fight.play(0))
        self.assertEqual(boss.slippery, 0)
        before = boss.hp
        fight.play(0)
        self.assertEqual(boss.hp, before - 20)  # 层数耗尽后按实际伤害结算


class SunderTests(unittest.TestCase):
    def test_sunder_damages_and_adds_wounds(self):
        monster = Monster("木桩", hp=30, max_hp=30,
                          script=[Intent("custom", handler="sunder",
                                         params={"damage": 26, "wound": 1})])
        fight = Fight([], [monster], seed=4)
        before = fight.hp
        fight.end_turn()
        self.assertEqual(fight.hp, before - 26)
        self.assertEqual([c.name for c in fight.discard], ["伤口"])
        # 伤口不能被打出.
        self.assertEqual(fight.to_state()["player"]["hand"], [])


class EncounterShapeTests(unittest.TestCase):
    def test_encounter_matches_recorded_numbers(self):
        boss = _vantom()
        self.assertEqual((boss.hp, boss.max_hp), (173, 173))
        self.assertEqual(boss.slippery, 8)
        self.assertEqual(boss.script[0].damage, 7)
        self.assertEqual((boss.script[1].damage, boss.script[1].hits), (6, 2))
        self.assertEqual(boss.script[2].params["damage"], 26)
        self.assertEqual(boss.script[3].strength, 2)

    def test_full_fight_is_reproducible(self):
        a = arena.run_fight(scenarios.starter("ironclad"), scenarios.encounter("vantom"),
                            seed=5, hp=98, max_hp=98)
        b = arena.run_fight(scenarios.starter("ironclad"), scenarios.encounter("vantom"),
                            seed=5, hp=98, max_hp=98)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertNotEqual(a.outcome, "incomplete:intents_exhausted")
        self.assertGreater(a.turns, 0)


if __name__ == "__main__":
    unittest.main()
