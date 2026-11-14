"""Boss 的考试科目 (来自 Spire Codex 的社区统计, 80 万局).

结论: 不是所有 Boss 都考格挡 —— 女王/帝皇蟹是格挡与爆发, 无厌沙虫是机制 (沙坑),
灵魂异鱼是清呼唤, 实验体/墨影幻灵是成长曲线。
"""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.strategy.bosses import BOSSES, advice_for, entry_fee, guide_for
from brain.strategy.context import StrategyContext
from brain.strategy.situation import situation


class GuideTests(unittest.TestCase):
    def test_queen_is_a_block_and_burst_check(self):
        boss = guide_for("女王")
        self.assertIsNotNone(boss)
        self.assertIn("格挡", boss["check"])
        self.assertEqual(boss["death_rate"], 18)

    def test_insatiable_is_a_mechanic_check(self):
        boss = guide_for("无厌沙虫")
        self.assertIn("机制", boss["check"])
        self.assertIn("狂乱逃离", boss["note"])

    def test_entry_fee_rises_by_act(self):
        self.assertLess(entry_fee(1), entry_fee(2))
        self.assertLess(entry_fee(2), entry_fee(3))

    def test_unknown_boss_returns_none(self):
        self.assertIsNone(guide_for("没有这个 Boss"))

    def test_all_bosses_have_a_check(self):
        for boss in BOSSES:
            self.assertTrue(boss.get("check"), boss.get("name"))


class InjectionTests(unittest.TestCase):
    def _state(self, boss_name: str, boss_id: str):
        return GameState.parse({
            "state_type": "map",
            "run": {"act": 3, "floor": 40, "ascension": 0},
            "map": {"boss": {"id": boss_id, "name": boss_name}},
            "player": {"character": "铁甲战士", "hp": 60, "max_hp": 84, "deck": []},
        })

    def test_situation_carries_the_boss_guide(self):
        payload = situation(self._state("女王", "QUEEN_BOSS"), StrategyContext())
        self.assertEqual(payload["boss_guide"]["boss"], "女王")
        self.assertIn("格挡", payload["boss_guide"]["check"])

    def test_unknown_boss_gives_empty_guide(self):
        payload = situation(self._state("未知 Boss", "UNKNOWN_BOSS"), StrategyContext())
        self.assertEqual(payload["boss_guide"], {})


if __name__ == "__main__":
    unittest.main()
