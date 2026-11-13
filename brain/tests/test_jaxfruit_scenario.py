"""真机场景: 第一幕闪光贾克斯果 + 飞蝇菌子.

2026-11-14 第 8 层阵亡战。复盘时怀疑"敌人叠力量、我们一直防"，于是把这一战
存成模拟器可加载的局面回测。实测每回合掉血 1/14/7/1/7/16/7/12/4，
贾克斯果的攻击始终是 3 点 (力量涨到 18 也没反映在伤害上)，所以按固定 3 点建模。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from brain.sim import arena
from brain.sim.scenario_file import load

SCENARIO = Path("scenarios/act1-jaxfruit-20261114.json")


class JaxfruitScenarioTests(unittest.TestCase):
    def test_scenario_loads(self):
        scenario = load(SCENARIO)
        self.assertEqual(scenario.player["hp"], 71)
        self.assertEqual(len(scenario.phases[0]), 2)
        names = [enemy.name for enemy in scenario.phases[0]]
        self.assertIn("闪光贾克斯果", names)
        self.assertIn("飞蝇菌子", names)

    def test_it_runs_and_is_a_hard_fight(self):
        # 这一战真机里死掉了: 模拟器也应该多数情况下打不过, 而不是轻松获胜.
        scenario = load(SCENARIO)
        batch = arena.run_scenario(scenario, runs=4)
        self.assertEqual(len(batch.results), 4)
        wins = sum(1 for result in batch.results if result.won)
        self.assertLessEqual(wins, 2, "这一战真机里打不过, 模拟器不该轻松获胜")
        for result in batch.results:
            self.assertGreater(result.turns, 5)


if __name__ == "__main__":
    unittest.main()
