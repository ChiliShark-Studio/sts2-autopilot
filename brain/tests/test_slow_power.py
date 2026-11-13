"""缓慢 (旧日雕像): 本回合每打出一张牌, 敌人从攻击牌受到的伤害 +10%.

回归数据取自 logs/run-20261112-215408.jsonl 的真实战斗帧:
第 4 回合连打三张打击 (6 伤, 敌人易伤), 敌人血量 87 -> 78 -> 69 -> 59.
"""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.combat.search import _Sim, _plan, decide_state


def state(*, hand, enemy_hp=87, slow=0, vulnerable=2, hp=47, energy=3, incoming=23):
    return {
        "state_type": "elite",
        "battle": {
            "round": 4, "turn": "player", "ready_for_action": True,
            "enemies": [{
                "entity_id": "BYGONE_EFFIGY_0", "name": "旧日雕像",
                "hp": enemy_hp, "max_hp": 127,
                "status": [{"id": "SLOW_POWER", "name": "缓慢", "amount": slow},
                           {"id": "VULNERABLE_POWER", "name": "易伤", "amount": vulnerable}],
                "intents": [{"type": "Attack", "damage": incoming, "hits": 1,
                             "label": str(incoming),
                             "description": f"这个敌人将要攻击造成{incoming}点伤害。"}],
            }],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "hand": hand, "potions": []},
    }


def strike(index=0):
    return {"index": index, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
            "target_type": "AnyEnemy"}


def defend(index=0):
    return {"index": index, "name": "防御", "cost": "1", "block": 5,
            "target_type": "Self"}


class SlowDamageTests(unittest.TestCase):
    def test_slow_stacks_after_each_card(self):
        parsed = GameState.parse(state(hand=[strike(0), strike(1), strike(2)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        enemy = sim.enemies[0]

        sim.play(0, 0)
        self.assertEqual(enemy.hp, 87 - 9)   # 6 * 1.5 易伤, 缓慢 0
        self.assertEqual(enemy.slow, 1)      # 打出一张后 +10%

        sim.play(0, 0)
        self.assertEqual(enemy.hp, 78 - 9)   # 9 * 1.1 = 9.9 -> 9
        self.assertEqual(enemy.slow, 2)

        sim.play(0, 0)
        self.assertEqual(enemy.hp, 69 - 10)  # 9 * 1.2 = 10.8 -> 10
        self.assertEqual(enemy.slow, 3)

    def test_block_cards_also_raise_slow(self):
        parsed = GameState.parse(state(hand=[defend(0), strike(1)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        enemy = sim.enemies[0]
        sim.play(0, None)                    # 防御也计入缓慢
        self.assertEqual(enemy.slow, 1)
        sim.play(0, 0)
        self.assertEqual(enemy.hp, 87 - 9)   # 9 * 1.1 -> 9

    def test_no_slow_power_means_no_bonus(self):
        raw = state(hand=[defend(0), strike(1)])
        raw["battle"]["enemies"][0]["status"] = [
            {"id": "VULNERABLE_POWER", "name": "易伤", "amount": 2}]
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, None)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 87 - 9)  # 没有缓慢加成


class OrderingTests(unittest.TestCase):
    def test_plan_maximises_damage_with_slow(self):
        # 防御 + 两张打击 (6 伤, 易伤): 把防御放在两刀之间能让第二刀吃到 +10%,
        # 最优总伤害是 9 + 9 = 18 或 9 + 10 = 19; 至少要拿到 19.
        hand = [defend(0), strike(1), strike(2)]
        raw = state(hand=hand, hp=60, incoming=8)
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        plan = _plan(sim, 10, 7)
        after = sim.clone()
        for pos, target in plan.sequence:
            self.assertTrue(after.play(pos, target))
        self.assertEqual(after.enemies[0].hp, 87 - 19)


if __name__ == "__main__":
    unittest.main()
