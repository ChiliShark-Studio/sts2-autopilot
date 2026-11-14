"""X 费牌的先后顺序: 高费低伤牌不该抢在旋风斩前面."""

import unittest

from brain.combat.search import decide_state

QUEEN = {"entity_id": "QUEEN_0", "name": "女王", "hp": 400, "max_hp": 400,
         "status": [], "intents": [{"type": "Attack", "damage": 15, "hits": 1}]}
WHIRL = {"name": "旋风斩+", "cost": "X", "damage": 8, "hits": 1, "cost_x": True, "hits_x": True,
         "target_type": "AllEnemies", "can_play": True,
         "description": "对所有敌人造成8点伤害X次。"}


def state(hand):
    return {"state_type": "boss",
            "battle": {"round": 3, "turn": "player", "ready_for_action": True,
                       "enemies": [dict(QUEEN)]},
            "player": {"hp": 60, "max_hp": 84, "energy": 4,
                       "status": [{"id": "STRENGTH_POWER", "name": "力量", "amount": 7}],
                       "hand": hand}}


def card(name, cost, damage, hits=1, target="AnyEnemy"):
    return {"name": name, "cost": str(cost), "damage": damage, "hits": hits,
            "target_type": target, "can_play": True, "description": "造成伤害。"}


class XCostOrderTests(unittest.TestCase):
    def test_cheap_high_damage_card_may_go_first(self):
        # 1 费 20 伤 + 3 段旋风斩 = 65 > 4 段 60, 先打它没问题.
        decision = decide_state(state([card("突破+", 1, 20, target="AllEnemies"),
                                       dict(WHIRL)]))
        self.assertEqual(decision.action.card.name, "突破+")

    def test_expensive_low_damage_card_should_not(self):
        # 2 费 17 伤 + 2 段 = 47, 不如 4 段 60: 应该先打旋风斩.
        decision = decide_state(state([card("痛击+", 2, 17), dict(WHIRL)]))
        self.assertEqual(decision.action.card.name, "旋风斩+")


if __name__ == "__main__":
    unittest.main()
