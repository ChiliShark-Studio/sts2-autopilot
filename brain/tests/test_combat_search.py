"""战斗工具测试: 关注关键行为, 不断言具体评分."""

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
        # 局面相同但濒临死亡时, 保命应优先于输出.
        decision = decide_state(state(hp=5, max_hp=70))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "防御")


class StrengthTests(unittest.TestCase):
    def test_strength_reaches_lethal(self):
        # 基础伤害 6 + 力量 3 = 伤害 9, 对方剩余 9 点生命, 应找到斩杀方案.
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


class LethalPlanningTests(unittest.TestCase):
    def test_weak_attack_is_not_reported_as_lethal(self):
        raw = state(enemy_hp=6, energy=1, powers=[{"id": "WEAK_POWER", "amount": 1}],
                    hand=[{"index": 0, "name": "打击", "cost": "1", "damage": 6,
                           "target_type": "AnyEnemy"}])
        decision = decide_state(raw)
        self.assertEqual(decision.action.kind, "play_card")
        self.assertNotIn("斩杀", decision.reason)

    def test_enemy_block_prevents_false_lethal(self):
        raw = state(enemy_hp=6, energy=1,
                    hand=[{"index": 0, "name": "打击", "cost": "1", "damage": 6,
                           "target_type": "AnyEnemy"}])
        raw["battle"]["enemies"][0]["block"] = 6
        self.assertNotIn("斩杀", decide_state(raw).reason)


if __name__ == "__main__":
    unittest.main()
