"""坚毅的随机消耗: 不能选择烧哪张, 只能决定要不要冒这个险."""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.tools.combat_search import _Sim, decide_state

GRIT = {
    "index": 0, "id": "TRUE_GRIT", "name": "坚毅", "type": "Skill", "cost": "1",
    "block": 7, "description": "获得7点格挡。 随机消耗1张牌。",
    "target_type": "Self", "can_play": True,
}
WOUND = {"index": 9, "id": "WOUND", "name": "伤口", "type": "Status", "cost": "0",
         "description": "不能被打出。", "can_play": False, "target_type": "None"}
CLEAVE_CARD = {"index": 1, "id": "WHIRLWIND", "name": "飞剑回旋镖", "type": "Attack",
               "cost": "1", "damage": 3, "hits": 3, "target_type": "AnyEnemy",
               "description": "随机对敌人造成3点伤害3次。"}


def state(hand, *, energy=3, enemy_hp=200, hp=70, incoming=10):
    return {
        "state_type": "monster",
        "battle": {
            "round": 2, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E_0", "name": "测试怪", "hp": enemy_hp,
                         "max_hp": enemy_hp,
                         "intents": [{"type": "Attack", "damage": incoming, "hits": 1,
                                      "label": str(incoming),
                                      "description": f"这个敌人将要攻击造成{incoming}点伤害。"}]}],
        },
        "player": {"hp": hp, "max_hp": 98, "energy": energy, "hand": hand, "potions": []},
    }


class ParseTests(unittest.TestCase):
    def test_parse_reads_random_exhaust(self):
        self.assertEqual(parse_card(GRIT)["random_exhaust"], 1)


class CostTests(unittest.TestCase):
    def test_cost_is_zero_when_hand_is_junk(self):
        from brain.tools.combat_search import _random_exhaust_cost

        parsed = GameState.parse(state([dict(WOUND, index=1), dict(WOUND, index=2)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertEqual(_random_exhaust_cost(list(sim.hand), 1), 0.0)

    def test_cost_grows_with_core_cards(self):
        from brain.tools.combat_search import _random_exhaust_cost

        parsed = GameState.parse(state([GRIT, CLEAVE_CARD, CLEAVE_CARD]))
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertGreater(_random_exhaust_cost(list(sim.hand), 1), 0.0)


class DecisionTests(unittest.TestCase):
    def test_not_played_when_it_would_burn_a_core_card(self):
        # 手里有飞剑回旋镖这种核心输出: 7 点格挡不值得赌掉它.
        decision = decide_state(state([GRIT, CLEAVE_CARD, CLEAVE_CARD],
                                      hp=70, incoming=10))
        names = [decision.action.card.name] if decision.action.kind == "play_card" else []
        self.assertNotIn("坚毅", names)

    def test_played_when_hand_is_junk(self):
        # 手里只有伤口: 烧掉它们毫无损失.
        decision = decide_state(state([GRIT, dict(WOUND, index=1), dict(WOUND, index=2)],
                                      hp=70, incoming=10))
        self.assertEqual(decision.action.card.name, "坚毅")

    def test_played_after_core_cards_are_used(self):
        # 先打掉核心牌, 手牌变干净后再打坚毅.
        hand = [GRIT, CLEAVE_CARD]
        decision = decide_state(state(hand, energy=2, hp=70, incoming=10))
        self.assertEqual(decision.action.card.name, "飞剑回旋镖")


if __name__ == "__main__":
    unittest.main()
