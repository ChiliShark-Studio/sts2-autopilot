"""全身撞击: 伤害等于打出时的格挡, 不是牌面上的 0."""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.combat.search import _Sim, decide_state

BODY_SLAM = {
    "index": 0, "id": "BODY_SLAM", "name": "全身撞击", "type": "Attack", "cost": "1",
    # 桥接按当前格挡算出 0, 真实伤害要在打出时结算.
    "damage": 0, "damage_base": 0, "block": 0, "hits": 1,
    "description": "造成你当前格挡值的伤害。 （造成0点伤害）",
    "target_type": "AnyEnemy", "can_play": True,
}


def defend(index=1, block=5):
    return {"index": index, "name": "防御", "cost": "1", "block": block,
            "target_type": "Self"}


def strike(index=1):
    return {"index": index, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
            "target_type": "AnyEnemy"}


def state(*, hand, energy=3, block=0, enemy_hp=100, hp=60, incoming=15):
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
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "block": block,
                   "hand": hand, "potions": []},
    }


class ParseTests(unittest.TestCase):
    def test_body_slam_is_flagged(self):
        parsed = parse_card(BODY_SLAM)
        self.assertTrue(parsed["body_slam"])
        self.assertEqual(parsed["damage"], 0)


class DamageTests(unittest.TestCase):
    def test_damage_equals_current_block(self):
        parsed = GameState.parse(state(hand=[dict(BODY_SLAM)], block=17))
        sim = _Sim.build(parsed.player, parsed.battle)
        enemy = sim.enemies[0]
        self.assertTrue(sim.play(0, 0))
        self.assertEqual(enemy.hp, 100 - 17)

    def test_block_gained_earlier_in_turn_counts(self):
        parsed = GameState.parse(state(hand=[dict(BODY_SLAM), defend()], block=0))
        sim = _Sim.build(parsed.player, parsed.battle)
        enemy = sim.enemies[0]
        sim.play(1, None)          # 防御 +5 格挡
        sim.play(0, 0)             # 全身撞击用上这 5 点
        self.assertEqual(enemy.hp, 100 - 5)

    def test_strength_and_vulnerable_apply(self):
        parsed = GameState.parse(state(hand=[dict(BODY_SLAM)], block=10, energy=3))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.strength = 3
        sim.enemies[0].vulnerable = 1
        sim.play(0, 0)
        # (10 + 3) * 1.5 = 19
        self.assertEqual(sim.enemies[0].hp, 100 - 19)


class DecisionTests(unittest.TestCase):
    def test_plan_uses_block_for_damage(self):
        # 有 18 格挡 + 两张防御: 最优是先垫格挡再撞, 伤害等于最终格挡.
        from brain.combat.search import _plan

        hand = [dict(BODY_SLAM), defend(), defend(2)]
        parsed = GameState.parse(state(hand=hand, block=18, incoming=15))
        sim = _Sim.build(parsed.player, parsed.battle)
        plan = _plan(sim, 10, 7)
        after = sim.clone()
        for pos, target in plan.sequence:
            self.assertTrue(after.play(pos, target))
        # 18 + 5 + 5 = 28 点格挡, 全部转成伤害.
        self.assertEqual(after.enemies[0].hp, 100 - 28)

    def test_block_first_then_slam(self):
        # 没格挡时: 先用防御垫格挡, 再用全身撞击换成伤害.
        hand = [dict(BODY_SLAM), defend()]
        decision = decide_state(state(hand=hand, block=0, energy=2, incoming=5))
        self.assertEqual(decision.action.card.name, "防御")


if __name__ == "__main__":
    unittest.main()
