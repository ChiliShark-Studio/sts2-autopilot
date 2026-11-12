"""独立战斗引擎和竞技场测试."""

from __future__ import annotations

import unittest

from brain.sim import arena, scenarios
from brain.sim.engine import DeckCard, Fight, Intent, Monster


def deck(n_strike: int = 5, n_defend: int = 5) -> list[DeckCard]:
    return ([DeckCard("打击", cost=1, damage=6)] * n_strike
            + [DeckCard("防御", cost=1, block=5, target="Self")] * n_defend)


class EngineTests(unittest.TestCase):
    def test_playing_a_card_spends_energy_and_damages(self):
        fight = Fight(deck(), [Monster("木桩", 30, 30, [Intent("attack", 0)])], seed=1)
        fight.start_turn()
        strike = next(i for i, c in enumerate(fight.hand) if c.name == "打击")
        before = fight.monsters[0].hp
        self.assertTrue(fight.play(strike))
        self.assertEqual(fight.monsters[0].hp, before - 6)
        self.assertEqual(fight.energy, 2)

    def test_cannot_play_beyond_energy(self):
        fight = Fight([DeckCard("重击", cost=5, damage=9)], [Monster("木桩", 30, 30)], seed=2)
        fight.start_turn()
        self.assertFalse(fight.play(0))

    def test_block_absorbs_enemy_attack(self):
        fight = Fight([], [Monster("木桩", 30, 30, [Intent("attack", 8)])], seed=3)
        fight.block = 5
        fight.end_turn()
        self.assertEqual(fight.hp, fight.max_hp - 3)

    def test_deck_reshuffles_when_empty(self):
        fight = Fight(deck(n_strike=3, n_defend=0), [Monster("木桩", 99, 99)], seed=4)
        fight.start_turn()
        self.assertEqual(len(fight.hand), 3)  # 只有 3 张卡牌
        fight.end_turn()
        fight.start_turn()
        self.assertEqual(len(fight.hand), 3)  # 弃牌堆重新洗牌后再次抽到

    def test_same_seed_same_result(self):
        a = arena.run_fight(deck(), scenarios.encounter("nibbit"), seed=7)
        b = arena.run_fight(deck(), scenarios.encounter("nibbit"), seed=7)
        self.assertEqual(a.to_dict(), b.to_dict())


class ArenaTests(unittest.TestCase):
    def test_beats_the_small_encounter(self):
        batch = arena.run_batch(scenarios.starter("silent"), lambda: scenarios.encounter("nibbit"), runs=5)
        self.assertEqual(batch.runs, 5)
        self.assertGreaterEqual(batch.win_rate, 0.8)

    def test_struggles_against_the_big_one(self):
        batch = arena.run_batch(scenarios.starter("silent"), lambda: scenarios.encounter("lagavulin"), runs=5)
        # 不硬性断言策略水平, 只确认竞技场能区分不同战斗.
        self.assertLessEqual(batch.win_rate, 0.8)

    def test_summary_shape(self):
        batch = arena.run_batch(scenarios.starter("ironclad"), lambda: scenarios.encounter("slimes"), runs=3)
        summary = batch.summary()
        for key in ("runs", "win_rate", "turns_avg", "damage_taken_avg", "hp_left_avg"):
            self.assertIn(key, summary)


if __name__ == "__main__":
    unittest.main()
