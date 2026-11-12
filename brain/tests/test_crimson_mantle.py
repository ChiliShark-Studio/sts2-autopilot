"""绯红披风: 每回合开始失去 1 生命获得 8 格挡, 不能当成"打出时 0 格挡"."""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.tools.combat_search import _Sim, decide_state

MANTLE = {
    "index": 0, "id": "CRIMSON_MANTLE", "name": "绯红披风", "type": "Power", "cost": "1",
    # 桥接对能力牌的结构化字段一律给 0, 真实数值只在描述里.
    "damage": 0, "block": 0, "hits": 1,
    "description": "在你的回合开始时，失去1点生命并获得8点格挡。",
    "rarity": "Rare", "can_play": True, "target_type": "Self",
}


def state(*, hand, energy=3, enemy_hp=100, hp=60, incoming=10):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E_0", "name": "测试怪", "hp": enemy_hp,
                         "max_hp": enemy_hp,
                         "intents": [{"type": "Attack", "damage": incoming, "hits": 1,
                                      "label": str(incoming),
                                      "description": f"这个敌人将要攻击造成{incoming}点伤害。"}]}],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "hand": hand, "potions": []},
    }


class ParseTests(unittest.TestCase):
    def test_recurring_block_is_not_immediate_block(self):
        parsed = parse_card(MANTLE)
        self.assertEqual(parsed["block"], 0)
        self.assertEqual(parsed["per_turn_block"], 8)
        self.assertEqual(parsed["per_turn_hp_cost"], 1)


class SimTests(unittest.TestCase):
    def test_playing_it_adds_future_block_value(self):
        parsed = GameState.parse(state(hand=[MANTLE]))
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertTrue(sim.play(0, None))
        self.assertGreater(sim.future_value, 0)
        # 当回合不会凭空多出格挡.
        self.assertEqual(sim.block, 0)


class DecisionTests(unittest.TestCase):
    def test_played_when_block_matters(self):
        decision = decide_state(state(hand=[MANTLE,
                                            {"index": 1, "name": "打击", "cost": "1",
                                             "damage": 6, "hits": 1,
                                             "target_type": "AnyEnemy"}],
                                      enemy_hp=120, hp=40, incoming=12))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "绯红披风")

    def test_does_not_delay_lethal(self):
        # 敌人只剩 5 血: 本回合必须斩杀 (先铺能力再补刀也算).
        decision = decide_state(state(hand=[MANTLE,
                                            {"index": 1, "name": "打击", "cost": "1",
                                             "damage": 6, "hits": 1,
                                             "target_type": "AnyEnemy"}],
                                      enemy_hp=5, hp=40, incoming=12))
        self.assertIn("斩杀", decision.reason)


if __name__ == "__main__":
    unittest.main()
