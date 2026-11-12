"""能力牌在模拟器里的效果: 力量引擎、消耗引擎、壁垒体系."""

from __future__ import annotations

import unittest

from brain.sim.engine import DeckCard, Fight, Intent, Monster
from brain.sim.ironclad import card


def dummy(hp: int = 200) -> Monster:
    return Monster("木桩", hp=hp, max_hp=hp, script=[Intent("attack", 0)])


def fight(deck: list[DeckCard], hp: int = 60, energy: int = 6) -> Fight:
    return Fight(deck, [dummy()], seed=1, hp=hp, max_hp=max(hp, 80), max_energy=energy)


def play_first(f: Fight, name: str) -> bool:
    for i, c in enumerate(f.hand):
        if c.name == name:
            return f.play(i)
    raise AssertionError(f"手牌里没有 {name}: {[c.name for c in f.hand]}")


class StrengthEngineTests(unittest.TestCase):
    def test_demon_form_grows_strength_each_turn(self):
        f = fight([card("恶魔形态")] + [card("打击")] * 5)
        f.start_turn()
        self.assertTrue(play_first(f, "恶魔形态"))
        f.end_turn()
        f.start_turn()
        self.assertEqual(f.strength, 2)      # 基础版每回合 +2
        f.end_turn()
        f.start_turn()
        self.assertEqual(f.strength, 4)

    def test_inflame_is_immediate(self):
        f = fight([card("燃烧")] + [card("打击")] * 5)
        f.start_turn()
        self.assertTrue(play_first(f, "燃烧"))
        self.assertEqual(f.strength, 2)

    def test_brand_costs_hp_and_gives_strength(self):
        f = fight([card("烙印")] + [card("打击")] * 5)
        f.start_turn()
        self.assertTrue(play_first(f, "烙印"))
        self.assertEqual(f.hp, 59)   # 起始 60 血, 烙印 -1
        self.assertEqual(f.strength, 1)

    def test_rupture_turns_hp_loss_into_strength(self):
        f = fight([card("破裂"), card("烙印")] + [card("打击")] * 4)
        f.start_turn()
        play_first(f, "破裂")
        play_first(f, "烙印")
        self.assertEqual(f.strength, 2)      # 烙印 +1, 破裂 +1


class ExhaustEngineTests(unittest.TestCase):
    def test_corruption_makes_skills_free_and_exhaust_them(self):
        f = fight([card("腐化"), card("防御")])
        f.start_turn()
        self.assertTrue(play_first(f, "腐化"))
        spent = f.energy                  # 腐化 3 费
        # 腐化让技能 0 费: 防御打出后能量不变.
        self.assertTrue(play_first(f, "防御"))
        self.assertEqual(f.energy, spent)
        self.assertEqual(f.exhausted, 1)
        self.assertEqual(f.block, 5)
        self.assertNotIn("防御", [c.name for c in f.discard])

    def test_feel_no_pain_blocks_on_exhaust(self):
        f = fight([card("无痛"), card("战栗")])
        f.start_turn()
        play_first(f, "无痛")
        play_first(f, "战栗")
        self.assertEqual(f.exhausted, 1)
        self.assertEqual(f.block, 3)

    def test_dark_embrace_draws_on_exhaust(self):
        f = fight([card("黑暗之拥"), card("战栗")] + [card("打击")] * 8)
        f.start_turn()
        # 抽到黑暗之拥和战栗再开始断言.
        while not (any(c.name == "黑暗之拥" for c in f.hand)
                   and any(c.name == "战栗" for c in f.hand)):
            f.end_turn()
            f.start_turn()
        play_first(f, "黑暗之拥")
        after_power = len(f.hand)
        play_first(f, "战栗")
        # 战栗 -1, 消耗触发抽 1.
        self.assertEqual(len(f.hand), after_power)

    def test_ashen_strike_scales_with_exhausted(self):
        f = fight([card("灰烬打击"), card("战栗"), card("战栗"), card("战栗")])
        f.start_turn()
        play_first(f, "战栗")
        play_first(f, "战栗")
        enemy = f.monsters[0]
        before = enemy.hp
        play_first(f, "灰烬打击")
        # 6 + 3 * 2 张已消耗 = 12
        # 6 + 3*2 = 12, 再吃 6 层易伤 1.5 倍 -> 18
        self.assertEqual(enemy.hp, before - 18)


class BlockEngineTests(unittest.TestCase):
    def test_barricade_keeps_block(self):
        f = fight([card("壁垒")] + [card("防御")] * 4)
        f.start_turn()
        play_first(f, "防御")             # 先防御, 拿 5 格挡
        self.assertEqual(f.block, 5)
        play_first(f, "壁垒")
        f.end_turn()
        f.start_turn()
        self.assertEqual(f.block, 5)    # 格挡没有清空

    def test_unmovable_doubles_first_block(self):
        f = fight([card("不可动摇"), card("防御"), card("防御")])
        f.start_turn()
        play_first(f, "不可动摇")
        play_first(f, "防御")
        self.assertEqual(f.block, 10)   # 5 翻倍
        play_first(f, "防御")
        self.assertEqual(f.block, 15)   # 第二张不翻倍

    def test_juggernaut_damages_when_gaining_block(self):
        f = fight([card("主宰"), card("防御")])
        f.start_turn()
        play_first(f, "主宰")
        enemy = f.monsters[0]
        before = enemy.hp
        play_first(f, "防御")
        self.assertEqual(enemy.hp, before - 5)

    def test_body_slam_uses_block(self):
        f = fight([card("全身撞击")] + [card("防御")] * 3)
        f.start_turn()
        play_first(f, "防御")
        play_first(f, "防御")
        enemy = f.monsters[0]
        before = enemy.hp
        play_first(f, "全身撞击")
        self.assertEqual(enemy.hp, before - 10)


class VulnerabilityTests(unittest.TestCase):
    def test_cruelty_raises_vulnerable_bonus(self):
        f = fight([card("残酷"), card("痛击"), card("打击")])
        f.start_turn()
        play_first(f, "残酷")
        play_first(f, "痛击")
        enemy = f.monsters[0]
        before = enemy.hp
        play_first(f, "打击")
        # 6 * (1.5 + 0.25) = 10.5 -> 10
        self.assertEqual(enemy.hp, before - 10)

    def test_bully_scales_with_vulnerable_stacks(self):
        f = fight([card("战栗"), card("欺凌")])
        f.start_turn()
        play_first(f, "战栗")           # 3 层易伤
        enemy = f.monsters[0]
        before = enemy.hp
        play_first(f, "欺凌")
        # 4 + 2 * 3 = 10, 再吃易伤 1.5 -> 15
        self.assertEqual(enemy.hp, before - 15)


if __name__ == "__main__":
    unittest.main()
