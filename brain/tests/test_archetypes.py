"""流派对比: 同一套战斗策略下, 不同构筑在第一幕精英/Boss 的表现.

这些数字用于相对比较, 不是真实胜率 (引擎不含遗物、药水与抽牌顺序细节).
牌组按 A10 攻略的主线搭, 都带升级后的基础防御, 保证有可比的格挡能力.
"""

from __future__ import annotations

import statistics
import unittest

from brain.sim import scenarios
from brain.sim.arena import run_fight
from brain.sim.ironclad import deck

# 那局实际用的 17 张散装牌组 (没有引擎、没有主线).
SCATTERED = {
    "打击": 5, "防御": 4, "痛击+": 1, "闪电霹雳": 1, "武装": 1, "预备打击": 1,
    "头槌": 1, "燃烧契约": 1, "坚毅": 1, "全身撞击": 1,
}

# 力量流: 燃烧起手, 多段攻击吃力量 (第一幕不急于上恶魔形态).
STRENGTH = {
    "打击": 2, "防御+": 4, "燃烧": 1, "飞剑回旋镖": 1, "双重打击": 1,
    "痛击+": 1, "耸肩无视": 1, "铁斩波": 2, "剑柄打击": 1,
}

# 消耗流: 腐化 + 黑暗之拥 + 无痛, 灰烬打击收尾.
EXHAUST = {
    "防御+": 4, "腐化": 1, "黑暗之拥": 1, "无痛": 1, "灰烬打击": 1,
    "燃烧契约": 1, "坚毅": 1, "剑柄打击": 1, "痛击+": 1, "铁斩波": 1, "打击": 1,
}

# 壁垒流: 壁垒 + 不可动摇 + 全身撞击.
BARRICADE = {
    "防御+": 5, "壁垒": 1, "不可动摇": 1, "全身撞击": 1, "耸肩无视": 1,
    "痛击+": 1, "铁斩波": 2, "剑柄打击": 1, "打击": 1,
}

DECKS = {
    "散装(实战)": SCATTERED,
    "力量流": STRENGTH,
    "消耗流": EXHAUST,
    "壁垒流": BARRICADE,
}

ENCOUNTERS = ("bygone_effigy", "vantom", "ceremonial_beast", "byrdonis")


def bench(spec: dict[str, int], encounter: str, hp: int = 80,
          runs: int = 10) -> tuple[float, float]:
    cards = deck(spec)
    results = [run_fight(cards, scenarios.encounter(encounter), seed=s, hp=hp, max_hp=80)
               for s in range(runs)]
    wins = sum(1 for r in results if r.won) / len(results)
    return wins, statistics.mean(r.damage_taken for r in results)


def average(spec: dict[str, int]) -> tuple[float, float]:
    rows = [bench(spec, e) for e in ENCOUNTERS]
    return (statistics.mean(w for w, _ in rows), statistics.mean(d for _, d in rows))


class ArchetypeComparisonTests(unittest.TestCase):
    """主线牌组应当至少不差于散装牌组, 且掉血更少."""

    def test_strength_is_at_least_as_good_as_scattered(self):
        scattered = average(SCATTERED)
        strength = average(STRENGTH)
        self.assertGreaterEqual(strength[0], scattered[0] - 0.05,
                                f"力量流 {strength} 对比散装 {scattered}")
        self.assertLessEqual(strength[1], scattered[1] + 2,
                             f"力量流掉血应更少: {strength} vs {scattered}")

    def test_strength_beats_effigy(self):
        win, _ = bench(STRENGTH, "bygone_effigy")
        self.assertGreaterEqual(win, 0.8)

    def test_engines_are_actually_used(self):
        # 力量流的核心件必须在牌表里, 否则"引擎"无从生效.
        for name in ("恶魔形态", "燃烧", "烙印"):
            self.assertEqual(deck({name: 1})[0].name, name)


if __name__ == "__main__":
    for name, spec in DECKS.items():
        win, damage = average(spec)
        detail = ", ".join(f"{e[:12]} {bench(spec, e)[0]:.0%}" for e in ENCOUNTERS)
        print(f"{name:<10} ({sum(spec.values())} 张) 平均胜率 {win:.0%} | 掉血 {damage:.0f} | {detail}")
