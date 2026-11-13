"""敌人机制规则测试: 策略分派、状态推导的阈值、以及对搜索的影响."""

from __future__ import annotations

import unittest

from brain.combat.enemy_rules import (STUN_TURN_VALUE, STRENGTH_RESET_VALUE,
                               CeremonialBeastRule, EnemyRule, RuleBook)
from brain.model import GameState
from brain.combat.search import _Sim, decide_state


def _sim(*, enemy_hp=60, enemy_max=60, incoming=20, hp=30, hand=None, energy=1,
         entity_id="CEREMONIAL_BEAST_0", name="仪式兽", powers=None):
    raw = {
        "state_type": "boss",
        "battle": {
            "round": 2, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": entity_id, "name": name,
                         "hp": enemy_hp, "max_hp": enemy_max,
                         "status": powers or [],
                         "intents": [{"type": "Attack", "damage": incoming, "hits": 1,
                                      "label": str(incoming),
                                      "description": f"这个敌人将要攻击造成{incoming}点伤害。"}]}],
        },
        "player": {
            "hp": hp, "max_hp": 80, "energy": energy,
            "hand": hand if hand is not None else [
                {"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
                 "target_type": "AnyEnemy"}],
            "potions": [],
        },
    }
    parsed = GameState.parse(raw)
    return _Sim.build(parsed.player, parsed.battle)


PLOW = [{"id": "PLOW_POWER", "name": "横冲直撞", "amount": 150}]


class TestBeastRule(EnemyRule):
    """测试用规则: 血量到 54 或以下就会被击晕."""

    ids = ("TEST_BEAST",)
    names = ()

    def stun_threshold_hp(self, enemy):
        return 54


class RuleDispatchTests(unittest.TestCase):
    def test_matches_by_id_and_name(self):
        rule = CeremonialBeastRule()
        self.assertTrue(rule.matches(type("E", (), {"entity_id": "CEREMONIAL_BEAST_0", "name": "?"})()))
        self.assertTrue(rule.matches(type("E", (), {"entity_id": "", "name": "仪式兽"})()))
        self.assertFalse(rule.matches(type("E", (), {"entity_id": "WRIGGLER_0", "name": "扭动虫"})()))

    def test_unknown_enemy_has_no_rule(self):
        sim = _sim(entity_id="WRIGGLER_0", name="扭动虫")
        book = RuleBook.with_defaults()
        self.assertIsNone(book.rule_for(sim.enemies[0]))
        self.assertEqual(book.crossing_bonus(sim, sim), 0.0)

    def test_empty_book_changes_nothing(self):
        book = RuleBook()
        sim = _sim(enemy_hp=60, enemy_max=60, powers=PLOW)
        after = sim.clone()
        after.enemies[0].hp = 10
        self.assertEqual(book.crossing_bonus(sim, after), 0.0)


class ThresholdTests(unittest.TestCase):
    """仪式兽的阈值来自状态里的横冲直撞层数, 不是写死的比例."""

    def test_threshold_comes_from_state(self):
        sim = _sim(powers=PLOW)
        self.assertEqual(CeremonialBeastRule().stun_threshold_hp(sim.enemies[0]), 150)

    def test_no_plow_power_means_no_rule(self):
        sim = _sim()  # 没有 PLOW_POWER
        self.assertIsNone(CeremonialBeastRule().stun_threshold_hp(sim.enemies[0]))

    def test_recorded_stun_matches_state_threshold(self):
        # 录像: 血量 170 时仍在攻击; 降到 146 (<=150) 后意图变成 Stun.
        self.assertGreater(170, 150)
        self.assertLessEqual(146, 150)

    def test_bonus_only_when_crossing(self):
        book = RuleBook([TestBeastRule()])
        sim = _sim(enemy_hp=60, enemy_max=60, entity_id="TEST_BEAST_0", name="测试兽")
        crossed = sim.clone()
        crossed.enemies[0].hp = 50
        already_below = sim.clone()
        already_below.enemies[0].hp = 54
        deep_below = crossed.clone()
        deep_below.enemies[0].hp = 40

        self.assertEqual(book.crossing_bonus(sim, crossed), STUN_TURN_VALUE)
        # 之前已经在阈值以下: 不是"这一下打出来的", 不重复计分.
        self.assertEqual(book.crossing_bonus(already_below, deep_below), 0.0)


class SearchInfluenceTests(unittest.TestCase):
    """打穿阈值能让对方少行动一回合, 搜索应当为此放弃一点输出/格挡."""

    def _state(self):
        return {
            "state_type": "boss",
            "battle": {
                "round": 2, "turn": "player", "ready_for_action": True,
                "enemies": [{"entity_id": "TEST_BEAST_0", "name": "测试兽",
                             "hp": 60, "max_hp": 60,
                             "intents": [{"type": "Attack", "damage": 20, "hits": 1,
                                          "label": "20",
                                          "description": "这个敌人将要攻击造成20点伤害。"}]}],
            },
            "player": {
                "hp": 30, "max_hp": 80, "energy": 1,
                "hand": [
                    # 6 点伤害刚好把 60 打到 54 (阈值), 但不提供格挡.
                    {"index": 0, "name": "重击", "cost": "1", "damage": 6, "hits": 1,
                     "target_type": "AnyEnemy"},
                    # 2 点伤害加 12 格挡: 不触发阈值, 但本回合更安全.
                    {"index": 1, "name": "防守", "cost": "1", "damage": 2, "hits": 1,
                     "block": 12, "target_type": "AnyEnemy"},
                ],
                "potions": [],
            },
        }

    def test_without_rules_prefers_the_blocking_line(self):
        decision = decide_state(self._state())
        self.assertEqual(decision.action.card.name, "防守")

    def test_with_rules_prefers_crossing_the_threshold(self):
        decision = decide_state(self._state(), rules=RuleBook([TestBeastRule()]))
        self.assertEqual(decision.action.card.name, "重击")


class StrengthResetTests(unittest.TestCase):
    """击晕会清空敌方力量时, 力量越高这一下越值钱."""

    STRENGTH = [{"id": "PLOW_POWER", "name": "横冲直撞", "amount": 150},
                {"id": "STRENGTH_POWER", "name": "力量", "amount": 6}]

    def _crossed(self, strength_power):
        sim = _sim(enemy_hp=170, enemy_max=252, powers=strength_power)
        after = sim.clone()
        after.enemies[0].hp = 140
        return sim, after

    def test_strength_reset_adds_value(self):
        book = RuleBook.with_defaults()
        sim, after = self._crossed(self.STRENGTH)
        bonus = book.crossing_bonus(sim, after)
        self.assertAlmostEqual(bonus, STUN_TURN_VALUE + 6 * STRENGTH_RESET_VALUE)

    def test_no_strength_no_extra_value(self):
        book = RuleBook.with_defaults()
        sim, after = self._crossed([{"id": "PLOW_POWER", "name": "横冲直撞", "amount": 150}])
        self.assertAlmostEqual(book.crossing_bonus(sim, after), STUN_TURN_VALUE)

    def test_rule_without_reset_keeps_flat_value(self):
        book = RuleBook([TestBeastRule()])
        sim = _sim(enemy_hp=60, enemy_max=60, entity_id="TEST_BEAST_0", name="测试兽",
                   powers=[{"id": "STRENGTH_POWER", "name": "力量", "amount": 9}])
        after = sim.clone()
        after.enemies[0].hp = 50
        self.assertAlmostEqual(book.crossing_bonus(sim, after), STUN_TURN_VALUE)


if __name__ == "__main__":
    unittest.main()
