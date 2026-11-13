"""玩家负面状态测试: 不可格挡 / 虚弱 / 缩小, 以及应急按钮的代价."""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.combat.search import _Sim, decide_state

EMERGENCY = {
    "index": 0, "name": "应急按钮+", "cost": "1", "block": 40, "target_type": "Self",
    "description": "获得40点格挡。 你在接下来的2回合内无法再从卡牌中获得格挡。 消耗。",
}
DEFEND = {"index": 1, "name": "防御", "cost": "1", "block": 5, "target_type": "Self",
          "description": "获得5点格挡。"}
STRIKE = {"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "description": "造成6点伤害。"}


def sim(*, hand, powers=None, incoming=10, hp=70):
    raw = {
        "state_type": "monster",
        "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                   "enemies": [{"entity_id": "E0", "name": "怪", "hp": 40, "max_hp": 40,
                                "intents": [{"type": "Attack", "damage": incoming, "hits": 1}]}]},
        "player": {"hp": hp, "max_hp": 70, "energy": 3, "hand": hand,
                   "status": powers or []},
    }
    state = GameState.parse(raw)
    return _Sim.build(state.player, state.battle)


class EmergencyButtonTests(unittest.TestCase):
    def test_parses_no_block_turns(self):
        parsed = GameState.parse({
            "state_type": "monster", "battle": {"turn": "player", "ready_for_action": True, "enemies": []},
            "player": {"hp": 70, "max_hp": 70, "energy": 3, "hand": [EMERGENCY]},
        })
        assert parsed.battle is not None
        self.assertEqual(parsed.battle.hand[0].no_block_turns, 2)

    def test_playing_it_sets_the_downside(self):
        s = sim(hand=[EMERGENCY], incoming=10)
        s.play(0, None)
        self.assertEqual(s.block, 40)
        self.assertEqual(s.no_block_turns, 2)

    def test_downside_lowers_value(self):
        plain = sim(hand=[DEFEND], incoming=10)
        after = plain.clone()
        after.block = 40
        button = plain.clone()
        button.block = 40
        button.no_block_turns = 2
        self.assertLess(button.value(), after.value())

    def test_prefers_cheap_block_over_overkill(self):
        decision = decide_state({
            "state_type": "monster",
            "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                       "enemies": [{"entity_id": "E0", "name": "怪", "hp": 40, "max_hp": 40,
                                    "intents": [{"type": "Attack", "damage": 10, "hits": 1}]}]},
            "player": {"hp": 70, "max_hp": 70, "energy": 3, "hand": [DEFEND, EMERGENCY]},
        })
        self.assertEqual(decision.action.card.name, "防御")


class PlayerDebuffTests(unittest.TestCase):
    def test_no_block_power_zeroes_card_block(self):
        s = sim(hand=[DEFEND], powers=[{"id": "NO_BLOCK_POWER", "name": "不可格挡", "amount": 1}])
        s.play(0, None)
        self.assertEqual(s.block, 0)

    def test_weak_reduces_attack_damage(self):
        s = sim(hand=[STRIKE], powers=[{"id": "WEAK_POWER", "name": "虚弱", "amount": 1}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 40 - 4)  # 6 * 0.75 = 4

    def test_shrink_reduces_attack_damage(self):
        s = sim(hand=[STRIKE], powers=[{"id": "SHRINK_POWER", "name": "缩小", "amount": 1}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 40 - 4)  # 6 * 0.7 = 4

    def test_both_stack(self):
        s = sim(hand=[STRIKE],
                powers=[{"id": "WEAK_POWER", "name": "虚弱", "amount": 1},
                        {"id": "SHRINK_POWER", "name": "缩小", "amount": 1}])
        s.play(0, 0)
        # 6 -> int(6*0.75)=4 -> int(4*0.7)=2
        self.assertEqual(s.enemies[0].hp, 40 - 2)


if __name__ == "__main__":
    unittest.main()
