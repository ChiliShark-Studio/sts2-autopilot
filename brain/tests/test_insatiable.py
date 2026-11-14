"""无厌沙虫 (THE_INSATIABLE): 沙坑见底即被吞下, 必须用狂乱逃离拖住.

机制来自灰机 wiki (2026-11 版本):
  * 第一回合「液化地面」: 4 层沙坑, 抽牌堆与弃牌堆各加 3 张狂乱逃离;
  * 沙坑每回合 -1, 归零时角色被吞下直接死亡, 瓶中精灵/蜥蜴尾巴都救不了;
  * 狂乱逃离: 打出后沙坑 +1, 但它的耗能每打出一次 +1。
这一战不能按"打到 0 血"来打, 必须先把逃离排进计划, 否则输出再高也会被吞。
"""

from __future__ import annotations

import unittest

from brain.combat.enemy_rules import RuleBook, TheInsatiableRule
from brain.combat.search import decide_state
from brain.model import GameState

ESCAPE = {"name": "狂乱逃离", "cost": "0", "type": "Skill", "can_play": True,
          "target_type": "Self", "description": "沙坑计数 +1。"}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}


def state(*, sandpit=2, hand=None, energy=3):
    status = ([{"id": "SAND_PIT_POWER", "name": "沙坑", "amount": sandpit}]
              if sandpit else [])
    return {
        "state_type": "boss",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "THE_INSATIABLE_0", "name": "无厌沙虫",
                         "hp": 321, "max_hp": 321, "status": [],
                         "intents": [{"type": "Attack", "damage": 16, "hits": 1}]}],
        },
        "player": {"hp": 70, "max_hp": 86, "energy": energy, "status": status,
                   "hand": hand if hand is not None else [dict(ESCAPE, index=0),
                                                          dict(STRIKE, index=1)]},
    }


class RuleTests(unittest.TestCase):
    def test_rule_matches_the_boss(self):
        parsed = GameState.parse(state())
        book = RuleBook.with_defaults()
        self.assertIsInstance(book.rule_for(parsed.battle.alive_enemies[0]),
                              TheInsatiableRule)

    def test_sandpit_is_read_from_player_powers(self):
        rule = TheInsatiableRule()
        self.assertEqual(rule.sandpit(GameState.parse(state(sandpit=3))), 3)
        # 没有沙坑能力时返回 None: 与沙坑见底(0)相反, 不能混为一谈.
        self.assertIsNone(rule.sandpit(GameState.parse(state(sandpit=0))))

    def test_escape_is_required_when_sandpit_is_low(self):
        rule = TheInsatiableRule()
        self.assertEqual(rule.required_card(GameState.parse(state(sandpit=2))), "狂乱逃离")

    def test_escape_starts_with_a_buffer(self):
        # 留 3 层缓冲: 卡到 1~2 才逃, 可能遇到"没抽到/耗能涨到打不起".
        rule = TheInsatiableRule()
        self.assertEqual(rule.required_card(GameState.parse(state(sandpit=3))), "狂乱逃离")

    def test_escape_is_not_required_when_sandpit_is_high(self):
        rule = TheInsatiableRule()
        self.assertEqual(rule.required_card(GameState.parse(state(sandpit=4))), "")

    def test_no_rule_for_other_enemies(self):
        raw = state()
        raw["battle"]["enemies"] = [{"entity_id": "NIBBIT_0", "name": "尼比特",
                                     "hp": 40, "max_hp": 40, "status": [],
                                     "intents": [{"type": "Attack", "damage": 6, "hits": 1}]}]
        book = RuleBook.with_defaults()
        self.assertIsNone(book.rule_for(GameState.parse(raw).battle.alive_enemies[0]))


class DecisionTests(unittest.TestCase):
    def test_it_escapes_before_attacking(self):
        decision = decide_state(state(sandpit=2))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "狂乱逃离")
        self.assertIn("沙坑", decision.reason)

    def test_it_attacks_when_sandpit_is_safe(self):
        decision = decide_state(state(sandpit=4))
        self.assertNotEqual(getattr(decision.action.card, "name", ""), "狂乱逃离")

    def test_without_escape_in_hand_it_plays_normally(self):
        decision = decide_state(state(sandpit=1, hand=[dict(STRIKE, index=0)]))
        self.assertEqual(decision.action.card.name, "打击")


if __name__ == "__main__":
    unittest.main()
