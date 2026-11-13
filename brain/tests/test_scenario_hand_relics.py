"""录像场景 (手牌 / 遗物 / 怪物) 的加载与重放.

``scenarios/underdocks-colony-20261114.json`` 是 2026-11-14 暗港精英阵亡战第一回合
的完整局面: 15/86 血、能量 3、给定手牌 5 张、遗物 3 个、鬼祟珊瑚群 75 血带硬化外壳 20.
场景格式因此需要支持"当前手牌"和"遗物", 否则只能从头洗牌, 重放不了当时的位置.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from brain.sim import scenarios
from brain.sim.engine import Fight, Intent
from brain.sim.scenario_file import load

RECORDING = (Path(__file__).resolve().parents[2]
             / "scenarios" / "underdocks-colony-20261114.json")
GARDENERS = (Path(__file__).resolve().parents[2]
             / "scenarios" / "underdocks-gardeners-20261114.json")


class RecordedScenarioTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load(RECORDING)

    def test_position_is_recorded(self):
        player = self.scenario.player
        self.assertEqual((player["hp"], player["max_hp"], player["energy"]), (15, 86, 3))
        self.assertEqual(len(self.scenario.deck), 19)
        self.assertEqual([c.name for c in self.scenario.starting_hand],
                         ["打击", "防御", "燃烧+", "防御", "打击"])
        colony = self.scenario.phases[0][0]
        self.assertEqual((colony.name, colony.hp, colony.hp_loss_cap), ("鬼祟珊瑚群", 75, 20))

    def test_relics_are_recorded_and_applied(self):
        names = [r["name"] for r in self.scenario.relics]
        self.assertEqual(names, ["燃烧之血", "卷轴箱", "臂甲"])
        self.assertTrue(self.scenario.player["relic_first_block_double"])

    def test_fight_starts_with_the_recorded_hand(self):
        fight = self.scenario.create_fight(seed=1)
        fight.start_turn()
        self.assertEqual([c.name for c in fight.hand],
                         ["打击", "防御", "燃烧+", "防御", "打击"])
        # 手牌里的 5 张已从抽牌堆移除, 不会重复抽到.
        self.assertEqual(len(fight.draw_pile), 14)

    def test_same_seed_replays_identically(self):
        def run(seed: int):
            fight = self.scenario.create_fight(seed=seed)
            fight.start_turn()
            strike = next(i for i, c in enumerate(fight.hand) if c.name == "打击")
            fight.play(strike, "SKULKING_COLONY_0")
            return fight.monsters[0].hp, fight.hand

        self.assertEqual(run(3)[0], run(3)[0])

    def test_shell_cap_applies_inside_the_recorded_fight(self):
        fight = self.scenario.create_fight(seed=2)
        fight.start_turn()
        # 一回合打三张打击也最多掉 18 点, 远低于 20 的上限; 换成重击才可能触顶.
        for _ in range(3):
            index = next((i for i, c in enumerate(fight.hand) if c.name == "打击"), None)
            if index is None:
                break
            fight.play(index, "SKULKING_COLONY_0")
        self.assertGreater(fight.monsters[0].hp_loss_left, 0)


class RelicTests(unittest.TestCase):
    def test_armlet_doubles_the_first_block_once_per_fight(self):
        from brain.sim.engine import DeckCard

        fight = Fight([DeckCard("防御", cost=0, block=5, target="Self")] * 4,
                      [scenarios.encounter("skulking_colony")[0]],
                      seed=1, relic_first_block_double=True)
        fight.start_turn()
        fight.play(0)
        self.assertEqual(fight.block, 10)     # 臂甲把第一次格挡翻倍
        fight.end_turn()
        fight.start_turn()
        fight.play(0)
        self.assertEqual(fight.block, 5)      # 之后不再翻倍


class GardenerRecordingTests(unittest.TestCase):
    """第二份真机录像: 4 只花园幽灵鳗 (胆小 6), 29 血进场, 牌组 16 张."""

    def setUp(self):
        self.scenario = load(GARDENERS)

    def test_position_is_recorded(self):
        player = self.scenario.player
        self.assertEqual((player["hp"], player["max_hp"], player["energy"]), (29, 80, 3))
        self.assertEqual(self.scenario.screen, "elite")
        self.assertEqual([c.name for c in self.scenario.starting_hand],
                         ["藏宝图", "飞剑回旋镖+", "耸肩无视", "御血术", "打击"])
        self.assertEqual(len(self.scenario.deck), 16)

    def test_all_four_gardeners_keep_their_skittish(self):
        gardeners = self.scenario.phases[0]
        self.assertEqual([m.hp for m in gardeners], [31, 28, 29, 26])
        self.assertTrue(all(m.skittish == 6 for m in gardeners))
        self.assertTrue(all(m.kind == "elite" for m in gardeners))

    def test_relics_are_recorded(self):
        self.assertEqual([r["name"] for r in self.scenario.relics],
                         ["燃烧之血", "松动羊毛剪", "地精之角"])

    def test_fight_runs_and_is_deterministic(self):
        from brain.sim.arena import run_scenario

        first = run_scenario(self.scenario, runs=2).results
        second = run_scenario(self.scenario, runs=2).results
        self.assertEqual([(r.won, r.turns, r.hp_left) for r in first],
                         [(r.won, r.turns, r.hp_left) for r in second])


BOSS = (Path(__file__).resolve().parents[2]
        / "scenarios" / "underdocks-soulfysh-20261114.json")


class SoulFyshRecordingTests(unittest.TestCase):
    """第三份真机录像: 第一幕 Boss 灵魂异鱼, 会用『策略』塞呼唤状态牌."""

    def setUp(self):
        self.scenario = load(BOSS)

    def test_position_is_recorded(self):
        player = self.scenario.player
        self.assertEqual((player["hp"], player["max_hp"], player["energy"]), (39, 83, 3))
        self.assertEqual(self.scenario.screen, "boss")
        self.assertEqual([c.name for c in self.scenario.starting_hand],
                         ["打击", "剑柄打击", "藏宝图", "防御", "燃烧契约"])
        boss = self.scenario.phases[0][0]
        self.assertEqual((boss.name, boss.hp, boss.kind), ("灵魂异鱼", 211, "boss"))

    def test_boss_shuffles_beckon_into_the_draw_pile(self):
        fight = self.scenario.create_fight(seed=1)
        fight.start_turn()
        fight.end_turn()          # 第一回合是『策略』: 塞 2 张呼唤
        cards = fight.draw_pile + fight.discard + fight.hand
        self.assertEqual(sum(1 for c in cards if c.id == "BECKON"), 2)
        self.assertTrue(all(c.end_turn_damage == 6 for c in cards if c.id == "BECKON"))


if __name__ == "__main__":
    unittest.main()
