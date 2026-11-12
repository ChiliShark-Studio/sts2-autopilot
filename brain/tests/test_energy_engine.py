"""薪火之源: 每回合开始 +1 能量, 单回合模型必须折算后续收益."""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.tools.combat_search import ENERGY_VALUE, _Sim, decide_state

PYRE = {
    "index": 0, "id": "PYRE", "name": "薪火之源", "type": "Power", "cost": "2",
    "description": "在回合开始时，获得[ironclad_energy_icon.png]。",
    "rarity": "Rare", "can_play": True,
}


def state(*, hand, energy=3, enemy_hp=60, hp=60, incoming=10) -> dict:
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
    def test_recurring_energy_is_not_immediate(self):
        parsed = parse_card(PYRE)
        # 打出时不会立即获得能量, 否则会被当成"2 费换 1 费".
        self.assertEqual(parsed["energy"], 0)
        self.assertEqual(parsed["per_turn_energy"], 1)


class SimTests(unittest.TestCase):
    def test_playing_it_grants_future_value(self):
        parsed = GameState.parse(state(hand=[PYRE], enemy_hp=80))
        sim = _Sim.build(parsed.player, parsed.battle)
        turns = sim.future_turns()
        self.assertTrue(sim.play(0, None))
        self.assertGreater(sim.future_value, 0)
        self.assertAlmostEqual(sim.future_value, ENERGY_VALUE * turns)
        # 能量只少了 2 点费用, 没有立即回能.
        self.assertEqual(sim.energy, 1)


class DecisionTests(unittest.TestCase):
    def test_engine_is_played_when_affordable(self):
        decision = decide_state(state(hand=[PYRE, {"index": 1, "name": "打击", "cost": "1",
                                                   "damage": 6, "hits": 1,
                                                   "target_type": "AnyEnemy"}],
                                      enemy_hp=80))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "薪火之源")

    def test_lethal_still_wins_over_engine(self):
        # 敌人只剩 6 血: 应该直接斩杀, 而不是先铺能量引擎.
        decision = decide_state(state(hand=[PYRE, {"index": 1, "name": "打击", "cost": "1",
                                                   "damage": 6, "hits": 1,
                                                   "target_type": "AnyEnemy"}],
                                      enemy_hp=6))
        self.assertEqual(decision.action.card.name, "打击")

    def test_cannot_play_without_enough_energy(self):
        # 2 费牌在 1 能量时不可打出, 搜索应结束回合而不是硬打.
        decision = decide_state(state(hand=[PYRE], enemy_hp=60, energy=1))
        self.assertNotEqual(decision.action.kind, "play_card")


if __name__ == "__main__":
    unittest.main()
