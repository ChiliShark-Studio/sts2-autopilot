"""覆甲 (岩石铠甲) 的多回合价值.

覆甲: 回合结束时获得 N 点格挡, 之后每回合减少 1 层。真机证据 (2026-11-14):
第 105 步手里有岩石铠甲与防御, 搜索只把覆甲算成"当回合 4 点格挡",
于是选了防御。把后续回合的递减收益补上之后, 长战斗里岩石铠甲才会被选中。
"""

from __future__ import annotations

import unittest

from brain.combat.search import decide_state

STONE = {"index": 0, "id": "STONE_ARMOR", "name": "岩石铠甲", "type": "Power", "cost": "1",
         "target_type": "Self", "can_play": True, "damage": 0, "block": 0, "hits": 1,
         "description": "获得4层覆甲。"}
DEFEND = {"index": 1, "name": "防御", "cost": "1", "block": 5, "hits": 1,
          "target_type": "Self", "can_play": True}


def state(enemy_hp, incoming=4, hp=80):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "怪", "hp": enemy_hp, "max_hp": enemy_hp,
                         "status": [], "intents": [{"type": "Attack", "damage": incoming, "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": 3, "status": [],
                   "hand": [dict(STONE, index=0), dict(DEFEND, index=1)]},
    }


class PlatedTests(unittest.TestCase):
    def test_long_fight_prefers_plated(self):
        # 55 血敌人: 估计还剩 2 回合, 覆甲值 4 + 3 = 7 点, 高于防御的 5 点.
        decision = decide_state(state(enemy_hp=55))
        self.assertEqual(decision.action.card.name, "岩石铠甲")

    def test_short_fight_prefers_immediate_block(self):
        # 15 血敌人: 一回合内解决, 覆甲只剩当回合的 4 点, 防御更划算.
        decision = decide_state(state(enemy_hp=15))
        self.assertEqual(decision.action.card.name, "防御")

    def test_plated_is_not_double_counted_within_the_turn(self):
        from brain.combat.search import _Sim
        from brain.model import GameState

        parsed = GameState.parse(state(enemy_hp=55))
        sim = _Sim.build(parsed.player, parsed.battle, hp_value_scale=1.0)
        before = sim.future_value
        sim.play(0, None)
        # 后续回合的格挡只按递减部分计入 (3 点), 当回合的 4 点走 block 结算.
        self.assertAlmostEqual(sim.future_value - before, 3 * sim.hp_weight(), places=6)
        self.assertEqual(sim.plated, 4)


if __name__ == "__main__":
    unittest.main()
