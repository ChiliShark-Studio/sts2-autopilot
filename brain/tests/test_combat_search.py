"""Combat tool tests: behaviour that matters, not exact scores."""

from __future__ import annotations

import unittest

from brain.tools.combat_search import CombatSearchTool, decide_state


def state(hp: int = 70, max_hp: int = 70, energy: int = 3, hand=None, enemy_hp: int = 20,
          incoming: int = 10, powers=None) -> dict:
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{
                "entity_id": "E_0", "name": "测试怪", "hp": enemy_hp, "max_hp": enemy_hp,
                "intents": [{"type": "Attack", "damage": incoming, "hits": 1}],
            }],
        },
        "player": {
            "hp": hp, "max_hp": max_hp, "energy": energy, "block": 0,
            "status": powers or [],
            "hand": hand or [
                {"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1},
                {"index": 1, "name": "防御", "cost": "1", "block": 5},
            ],
        },
    }


def kind(raw: dict) -> str:
    decision = decide_state(raw)
    return decision.action.kind if decision.action else "wait"


class AdaptiveWeightTests(unittest.TestCase):
    def test_healthy_prefers_damage(self):
        self.assertEqual(kind(state(hp=70, max_hp=70)), "play_card")

    def test_low_hp_values_block(self):
        # Same board, but one hit from death: surviving outweighs damage.
        decision = decide_state(state(hp=5, max_hp=70))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "防御")


class StrengthTests(unittest.TestCase):
    def test_strength_reaches_lethal(self):
        # 6 base + 3 strength = 9 damage vs 9 HP -> lethal shortcut fires.
        raw = state(hand=[{"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1}],
                    enemy_hp=9, powers=[{"name": "力量", "amount": 3}])
        decision = decide_state(raw)
        self.assertIn("斩杀", decision.reason)

    def test_strength_increases_modelled_damage(self):
        raw = state(hand=[{"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1}],
                    enemy_hp=30, powers=[{"name": "力量", "amount": 3}])
        decision = decide_state(raw)
        self.assertIn("6+3", decision.reason)


class ShapeTests(unittest.TestCase):
    def test_waits_when_not_ready(self):
        raw = state()
        raw["battle"]["ready_for_action"] = False
        self.assertEqual(kind(raw), "wait")

    def test_no_playable_cards_ends_turn(self):
        raw = state(energy=0)
        self.assertEqual(kind(raw), "end_turn")


if __name__ == "__main__":
    unittest.main()
