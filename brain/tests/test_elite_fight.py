"""真机战斗回归: 第 9 层精英「多尼斯异鸟」的致败回合.

这局在第 9 层精英里死亡: 14 血面对多尼斯异鸟的攻击, 战斗算法选择全力输出,
把 3 瓶药水带到死. 复盘发现两个建模错误:

  * 桥接的 ``intent.damage`` 是基础伤害, 不含敌方力量与虚弱;
    实际这一下是 5×3=15, 算法只看到 3×3=9, 于是把必死回合判成安全.
  * 格挡药水只在"不喝就会死"时使用, 低血量时的减伤价值从未被评估.

状态取自 ``data/combat`` 同源的真实录像, 保存在 ``brain/tests/data/final_fight.json``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brain.model import GameState
from brain.combat.search import decide_state

FIXTURE = Path(__file__).with_name("data") / "final_fight.json"


def state(step: str) -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[step]


class IncomingDamageTests(unittest.TestCase):
    """来袭伤害必须按游戏结算后的数值计算."""

    def test_enemy_strength_is_included(self):
        # 基础 3 点、力量 2 点、攻击 3 次 = 5×3 = 15.
        parsed = GameState.parse(state("610"))
        self.assertEqual(parsed.battle.incoming, 15)

    def test_weak_reduction_is_included(self):
        from brain.effects import resolved_attack

        intent = {"type": "Attack", "damage": 4, "hits": 1, "label": "3",
                  "description": "这个敌人将要攻击造成3点伤害。"}
        self.assertEqual(resolved_attack(intent), (3, 1))

    def test_base_value_is_used_when_display_missing(self):
        from brain.effects import resolved_attack

        intent = {"type": "Attack", "damage": 7, "hits": 2}
        self.assertEqual(resolved_attack(intent), (7, 2))


class RingingTests(unittest.TestCase):
    """仪式兽的昏眩: 本回合只能打出 1 张牌."""

    FIXTURE = Path(__file__).with_name("data") / "ringed_turn.json"

    def _state(self) -> dict:
        return json.loads(self.FIXTURE.read_text(encoding="utf-8"))["201"]

    def test_ringing_limits_plays_to_one_card(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        parsed = GameState.parse(self._state())
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertEqual(sim.play_limit, 1)
        pos, target = sim.legal()[0]
        self.assertTrue(sim.play(pos, target))
        # 出过一张牌后, 本回合不应再有任何合法出牌.
        self.assertEqual(sim.legal(), [])

    def test_ringed_turn_blocks_instead_of_dying(self):
        # 12 血面对 15 点攻击: 只有一张牌的机会, 应该用来格挡而不是打输出.
        decision = decide_state(self._state())
        self.assertEqual(decision.action.kind, "play_card")
        self.assertIn("格挡", decision.reason)
        self.assertIn("耸肩无视", decision.reason)


class PotionUseTests(unittest.TestCase):
    """濒死回合要用药水保命, 而不是继续输出."""

    def test_real_frame_prefers_survival(self):
        # 补上死亡惩罚后, 这一帧不会再为了打满输出而选择被打死.
        from brain.model import GameState
        from brain.combat.search import _Sim, _plan

        parsed = GameState.parse(state("610"))
        sim = _Sim.build(parsed.player, parsed.battle)
        plan = _plan(sim, 10, 7)
        self.assertGreater(plan.hp_left, 0)

    def test_uses_block_potion_when_no_card_survives(self):
        # 手牌只有 5 点格挡, 10 血吃 15 点伤害; 12 点格挡药水才能救回来.
        raw = {
            "state_type": "elite",
            "battle": {
                "round": 3, "turn": "player", "ready_for_action": True,
                "enemies": [{"entity_id": "E_0", "name": "仪式兽", "hp": 60, "max_hp": 252,
                             "intents": [{"type": "Attack", "damage": 15, "hits": 1,
                                          "label": "15",
                                          "description": "这个敌人将要攻击造成15点伤害。"}]}],
            },
            "player": {
                "hp": 10, "max_hp": 80, "energy": 1, "block": 0,
                "hand": [{"index": 0, "name": "防御", "cost": "1", "block": 5}],
                "potions": [{"slot": 0, "id": "BLOCK_POTION", "name": "格挡药水",
                             "description": "获得12点格挡。", "target_type": "Self",
                             "can_use_in_combat": True}],
            },
        }
        decision = decide_state(raw)
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("保命", decision.reason)

    def test_uses_potion_at_end_of_turn_instead_of_waiting(self):
        # 能量已用完, 但药水仍能挡住这一击.
        decision = decide_state(state("614"))
        self.assertEqual(decision.action.kind, "use_potion")

    def test_keeps_potion_when_no_plan_survives(self):
        # 5 血面对 18 点攻击, 12 点格挡也救不回来, 不应浪费药水.
        decision = decide_state(state("627"))
        self.assertFalse(decision.action is not None and decision.action.kind == "use_potion")


if __name__ == "__main__":
    unittest.main()
