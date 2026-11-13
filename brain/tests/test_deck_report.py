"""牌组体检与 A10 构筑主线提示."""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.strategy.situation import ENGINE_CARDS, deck_report, situation
from brain.strategy.prompts import character_prompt
from brain.strategy.context import StrategyContext


def state(*cards, hp=50, max_hp=80):
    hand = [{"index": i, "name": n, "cost": "1", "description": d}
            for i, (n, d) in enumerate(cards)]
    return {
        "state_type": "card_reward",
        "run": {"act": 1, "floor": 6, "ascension": 10},
        "player": {"character": "铁甲战士", "hp": hp, "max_hp": max_hp, "gold": 99,
                   "deck": hand},
        "card_reward": {"cards": []},
    }


STRIKE = ("打击", "造成6点伤害。")
DEFEND = ("防御", "获得5点格挡。")
DEMON = ("恶魔形态", "在你的回合开始时，获得3点力量。")
WHIRL = ("旋风斩", "对所有敌人造成5点伤害X次。")
BODY_SLAM = ("全身撞击", "造成你当前格挡值的伤害。")


class ReportTests(unittest.TestCase):
    def test_reports_missing_engine(self):
        st = GameState.parse(state(STRIKE, STRIKE, DEFEND))
        report = deck_report(st)
        self.assertEqual(report["size"], 3)
        self.assertEqual(report["strikes"], 2)
        self.assertIn("引擎", report["missing"])
        self.assertEqual(report["engine_cards"], [])

    def test_engine_is_detected(self):
        st = GameState.parse(state(STRIKE, DEMON, WHIRL))
        report = deck_report(st)
        self.assertIn("恶魔形态", report["engine_cards"])
        self.assertIn("旋风斩", report["payoff_cards"])
        self.assertNotIn("引擎", report["missing"])

    def test_body_slam_without_barricade_is_flagged(self):
        st = GameState.parse(state(STRIKE, BODY_SLAM, DEFEND))
        report = deck_report(st)
        self.assertTrue(any("壁垒" in m for m in report["missing"]))

    def test_damage_per_turn_estimate(self):
        # 10 张牌里 4 张 6 伤打击: 24 * 5 / 10 = 12.
        st = GameState.parse(state(*([STRIKE] * 4 + [DEFEND] * 6)))
        self.assertAlmostEqual(deck_report(st)["damage_per_turn"], 12.0)

    def test_report_is_in_situation(self):
        st = GameState.parse(state(STRIKE, DEMON))
        data = situation(st, StrategyContext())
        self.assertIn("deck_report", data)
        self.assertIn("damage_per_turn", data["deck_report"])


class PromptTests(unittest.TestCase):
    def test_prompt_states_archetypes_and_a10_rule(self):
        prompt = character_prompt(GameState.parse(state(STRIKE)))
        for keyword in ("主线", "力量成长", "消耗引擎", "壁垒", "A10"):
            self.assertIn(keyword, prompt)

    def test_prompt_forbids_mixed_half_builds(self):
        prompt = character_prompt(GameState.parse(state(STRIKE)))
        self.assertIn("不要混搭", prompt)

    def test_engine_set_covers_guide_cards(self):
        for name in ("恶魔形态", "燃烧", "烙印", "腐化", "黑暗之拥", "无痛", "壁垒"):
            self.assertIn(name, ENGINE_CARDS)


    def test_prompt_states_relic_priority(self):
        prompt = character_prompt(GameState.parse(state(STRIKE)))
        self.assertIn("遗物", prompt)
        self.assertIn("优先级低于", prompt)
        # 遗物不能花光金币: 要留下一次删牌的钱.
        self.assertIn("next_removal_price", prompt)


class ComboTests(unittest.TestCase):
    """两件套: 缺一半时优先补另一半."""

    def test_missing_half_is_reported(self):
        st = GameState.parse(state(("壁垒", "格挡不再消失。"), DEFEND))
        report = deck_report(st)
        self.assertEqual(report["combos_ready"], [])
        self.assertTrue(any("全身撞击" in m for m in report["combos_missing"]))

    def test_missing_half_works_in_both_directions(self):
        st = GameState.parse(state(BODY_SLAM, DEFEND))
        report = deck_report(st)
        self.assertTrue(any("壁垒" in m for m in report["combos_missing"]))

    def test_ready_combo_is_reported_once(self):
        st = GameState.parse(state(("壁垒", "格挡不再消失。"), BODY_SLAM))
        report = deck_report(st)
        self.assertEqual(len(report["combos_ready"]), 1)
        self.assertEqual(report["combos_missing"], [])


class CounterTests(unittest.TestCase):
    """对策卡按威胁归类, 每类 1~2 张即可."""

    def test_counter_coverage_groups_by_threat(self):
        st = GameState.parse(state(WHIRL, ("闪电霹雳", "对所有敌人造成4点伤害。")))
        report = deck_report(st)
        self.assertIn("多敌", report["counter_coverage"])
        self.assertGreaterEqual(len(report["counter_coverage"]["多敌"]), 2)

    def test_prompt_mentions_combo_and_counter_rules(self):
        prompt = character_prompt(GameState.parse(state(STRIKE)))
        self.assertIn("组合优先", prompt)
        self.assertIn("对策卡", prompt)


if __name__ == "__main__":
    unittest.main()
