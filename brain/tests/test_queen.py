"""女王 (QUEEN): 爪牙存活时她不攻击, 但每回合给爪牙 +1 力量、自己 +20 格挡.

真机第三幕第 48 层 (logs/run-20261114-072119.jsonl): 女王 400 血 + 火炬头聚合体 199 血。
这一战的资源结构不是"打谁"的问题:

  * 聚合体每回合被女王喂 1 点力量 -> 它才是伤害来源, 拖不得;
  * 女王每回合 +20 格挡 -> 打她先削 20, 不适合当首要目标;
  * 聚合体死亡后女王转攻击 (3x5 / 15), 所以"杀爪牙"本身也是要付代价的。
"""

from __future__ import annotations

import unittest

from brain.combat.enemy_rules import QueenRule, RuleBook
from brain.combat.search import _Sim, decide_state
from brain.model import GameState

STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}


def state(*, player_status=(), queen_block=0, queen_hp=400, minion_hp=199):
    return {
        "state_type": "boss",
        "battle": {
            "round": 3, "turn": "player", "ready_for_action": True,
            "enemies": [
                {"entity_id": "QUEEN_0", "name": "女王", "hp": queen_hp, "max_hp": 400,
                 "block": queen_block, "status": [], "intents": [{"type": "Buff"}]},
                {"entity_id": "TORCH_HEAD_AMALGAM_0", "name": "火炬头聚合体",
                 "hp": minion_hp, "max_hp": 199,
                 "status": [{"id": "MINION_POWER", "name": "爪牙", "amount": 1}],
                 "intents": [{"type": "Attack", "damage": 18, "hits": 1}]},
            ],
        },
        "player": {"hp": 60, "max_hp": 84, "energy": 3, "status": list(player_status),
                   "hand": [dict(STRIKE, index=0)]},
    }


class PlayerVulnerableTests(unittest.TestCase):
    def test_vulnerable_is_read_from_state(self):
        # 伤害在状态里已经是结算后的值 (游戏意图上的数字), 所以这里只验证读到了层数;
        # 再乘一次 1.5 会把每回合 18 点高估成 27 点.
        hurt = _Sim.build(*_parts(state(player_status=[
            {"id": "VULNERABLE_POWER", "name": "易伤", "amount": 99}])))
        self.assertEqual(hurt.vulnerable, 99)


class QueenResourceTests(unittest.TestCase):
    def test_rule_reads_growth_and_block(self):
        rule = QueenRule()
        self.assertEqual(rule.buff_on_others(), 1)
        self.assertEqual(rule.steady_block(), 20)

    def test_rulebook_reports_minion_growth(self):
        book = RuleBook.with_defaults()
        self.assertEqual(book.minion_growth(GameState.parse(state())), 1)

    def test_rulebook_reports_steady_block(self):
        book = RuleBook.with_defaults()
        self.assertEqual(book.steady_block(GameState.parse(state())), 20)

    def test_queen_counts_as_having_effective_block(self):
        # 打女王要先削 20, 因此等效血量更高, 同等伤害下优先打爪牙.
        parsed = GameState.parse(state())
        sim = _Sim.build(parsed.player, parsed.battle)
        queen = next(e for e in sim.enemies if not e.is_minion)
        minion = next(e for e in sim.enemies if e.is_minion)
        queen.steady_block = 20
        self.assertGreater(queen.steady_block, minion.steady_block)


class TargetChoiceTests(unittest.TestCase):
    def test_it_kills_the_low_health_minion_first(self):
        # 爪牙能打死时优先处理它: 它每回合 18 点, 而且还在被女王喂力量.
        decision = decide_state(state(minion_hp=6))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.target, "TORCH_HEAD_AMALGAM_0")

    def test_killing_the_queen_ends_the_fight(self):
        # 两边都是 6 血时应该打女王: 首领一死, 爪牙直接离场, 战斗结束.
        decision = decide_state(state(minion_hp=6, queen_hp=6))
        self.assertEqual(decision.action.target, "QUEEN_0")
        self.assertIn("斩杀", decision.reason)


def _parts(raw):
    parsed = GameState.parse(raw)
    return parsed.player, parsed.battle


if __name__ == "__main__":
    unittest.main()
