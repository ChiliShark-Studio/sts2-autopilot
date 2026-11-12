"""旧日雕像的模拟: 沉睡、苏醒(+10 力量)、缓慢.

数值来自 Spire Codex (127 血 / 招式 沉睡·苏醒·斩击 13 / 固有 Slow),
缓慢倍率用 logs/run-20261112-215408.jsonl 的真实掉血校准.
"""

from __future__ import annotations

import unittest

from brain.sim import arena, scenarios
from brain.sim.engine import DeckCard, Fight, Intent, Monster


def _effigy() -> Monster:
    return scenarios.encounter("bygone_effigy")[0]


def strike(damage: int = 6) -> DeckCard:
    return DeckCard("打击", cost=1, damage=damage)


def defend() -> DeckCard:
    return DeckCard("防御", cost=1, block=5, target="Self")


class SleepTests(unittest.TestCase):
    def test_sleeping_enemy_does_nothing(self):
        fight = Fight([defend()], [_effigy()], seed=1)
        fight.start_turn()
        fight.end_turn()
        self.assertEqual(fight.hp, fight.max_hp)   # 沉睡回合不掉血
        self.assertEqual(fight.monsters[0].step, 1)

    def test_wake_grants_strength_then_attacks_for_23(self):
        fight = Fight([], [_effigy()], seed=2)
        fight.start_turn()
        fight.end_turn()          # 沉睡
        fight.start_turn()
        fight.end_turn()          # 苏醒 +10 力量
        boss = fight.monsters[0]
        self.assertEqual(boss.strength, 10)
        self.assertEqual(boss.attack_damage(), 23)   # 13 + 10
        fight.start_turn()
        fight.end_turn()          # 斩击
        self.assertEqual(fight.hp, fight.max_hp - 23)

    def test_state_shows_sleep_intent(self):
        fight = Fight([], [_effigy()], seed=3)
        fight.start_turn()
        enemies = fight.to_state()["battle"]["enemies"]
        self.assertEqual(enemies[0]["intents"][0]["type"], "Sleep")


class SlowTests(unittest.TestCase):
    def _combat(self, hp=87, vulnerable=2):
        boss = Monster("旧日雕像", hp=hp, max_hp=127,
                       script=[Intent("sleep")], entity_id="BYGONE_EFFIGY_0",
                       has_slow=True, vulnerable=vulnerable)
        return Fight([strike()] * 3, [boss], seed=4), boss

    def test_damage_matches_recorded_fight(self):
        # 真实帧: 87 -> 78 -> 69 -> 59 (易伤, 每打一张缓慢 +10%).
        fight, boss = self._combat()
        fight.start_turn()
        for _ in range(3):
            fight.play(0)
        self.assertEqual(boss.hp, 59)
        self.assertEqual(boss.slow, 3)

    def test_slow_resets_each_turn(self):
        fight, boss = self._combat()
        fight.start_turn()
        fight.play(0)
        self.assertEqual(boss.slow, 1)
        fight.end_turn()
        fight.start_turn()
        self.assertEqual(boss.slow, 0)

    def test_non_attack_cards_also_stack_slow(self):
        fight, boss = self._combat()
        fight.start_turn()
        fight.play(0)                      # 打击
        before = boss.hp
        fight.play(0)                      # 再打一张
        self.assertEqual(boss.slow, 2)
        self.assertLess(boss.hp, before)


class EncounterShapeTests(unittest.TestCase):
    def test_numbers_match_wiki(self):
        boss = _effigy()
        self.assertEqual((boss.hp, boss.max_hp), (127, 127))
        self.assertTrue(boss.has_slow)
        self.assertEqual(boss.script[0].type, "sleep")
        self.assertEqual(boss.script[1].strength, 10)
        self.assertEqual(boss.script[2].damage, 13)

    def test_full_fight_reproducible(self):
        a = arena.run_fight(scenarios.starter("ironclad"), scenarios.encounter("bygone_effigy"),
                            seed=5, hp=80, max_hp=80)
        b = arena.run_fight(scenarios.starter("ironclad"), scenarios.encounter("bygone_effigy"),
                            seed=5, hp=80, max_hp=80)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertGreater(a.turns, 0)


if __name__ == "__main__":
    unittest.main()
