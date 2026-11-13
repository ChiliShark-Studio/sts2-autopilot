"""呼唤 (BECKON) 建模测试.

真机证据 (2026-11-14 第一幕 Boss 灵魂异鱼): Boss 用"策略"意图塞状态牌进手牌,
游戏内文本为"在你的回合结束时，如果这张牌在你的手牌中，你失去6点生命"。

搜索如果不算这一条, 就会把 呼唤 一直留在手里, 每回合白掉 6 点血.
"""

from __future__ import annotations

import unittest

from brain.combat.search import _Sim
from brain.model import GameState

BECKON = {"id": "BECKON", "name": "呼唤", "type": "Status", "cost": "1",
          "target_type": "None", "can_play": True, "damage": 0, "block": 0, "hits": 1}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "target_type": "AnyEnemy", "can_play": True}


def sim(*, hand, hp=60, incoming=0):
    raw = {
        "state_type": "boss",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "SOUL_FYSH_0", "name": "灵魂异鱼", "hp": 200,
                         "max_hp": 200, "status": [],
                         "intents": [{"type": "Attack", "damage": incoming, "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": 3, "hand": hand, "status": []},
    }
    state = GameState.parse(raw)
    return _Sim.build(state.player, state.battle, hp_value_scale=1.0)


class BeckonTests(unittest.TestCase):
    def test_holding_it_costs_more_than_not_holding_it(self):
        plain = sim(hand=[dict(STRIKE, index=0)])
        holding = sim(hand=[dict(BECKON, index=0), dict(STRIKE, index=1)])
        # 同样的牌面, 手里多一张 呼唤 应当更差 (回合结束掉 6 点).
        self.assertLess(holding.value(), plain.value())

    def test_playing_it_removes_the_penalty(self):
        holding = sim(hand=[dict(BECKON, index=0), dict(STRIKE, index=1)])
        played = holding.clone()
        self.assertTrue(played.play(0, None))
        self.assertEqual(
            [c.name for c in played.hand], ["打击"])
        # 打完 呼唤 之后只剩牌面差异, 不应该再有额外的掉血惩罚.
        plain = sim(hand=[dict(STRIKE, index=0)])
        self.assertAlmostEqual(played.value(), plain.value(), places=6)

    def test_two_copies_stack(self):
        one = sim(hand=[dict(BECKON, index=0)])
        two = sim(hand=[dict(BECKON, index=0), dict(BECKON, index=1)])
        plain = sim(hand=[])
        # 两张的惩罚是单张的两倍.
        self.assertAlmostEqual((plain.value() - one.value()) * 2,
                               plain.value() - two.value(), places=6)

    def test_holding_it_can_kill(self):
        # 5 血 + 手里一张 呼唤: 回合结束就死, 必须有死亡惩罚.
        s = sim(hand=[dict(BECKON, index=0)], hp=5)
        healthy = sim(hand=[dict(BECKON, index=0)], hp=20)
        self.assertLess(s.value(), healthy.value() - 900)


class BeckonPolicyTests(unittest.TestCase):
    """本回合打不死时先清呼唤; 能斩杀时仍然先斩杀."""

    def _raw(self, enemy_hp, hand):
        return {
            "state_type": "boss",
            "battle": {
                "round": 1, "turn": "player", "ready_for_action": True,
                "enemies": [{"entity_id": "SOUL_FYSH_0", "name": "灵魂异鱼", "hp": enemy_hp,
                             "max_hp": enemy_hp, "status": [],
                             "intents": [{"type": "Attack", "damage": 16, "hits": 1}]}],
            },
            "player": {"hp": 60, "max_hp": 80, "energy": 3, "hand": hand, "status": []},
        }

    def test_clears_beckon_when_it_cannot_kill(self):
        from brain.combat.search import decide_state

        raw = self._raw(200, [dict(BECKON, index=0), dict(STRIKE, index=1)])
        decision = decide_state(raw)
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "呼唤")

    def test_still_takes_the_lethal_play(self):
        from brain.combat.search import decide_state

        raw = self._raw(6, [dict(BECKON, index=0), dict(STRIKE, index=1)])
        decision = decide_state(raw)
        self.assertEqual(decision.action.card.name, "打击")
        self.assertIn("斩杀", decision.reason)


if __name__ == "__main__":
    unittest.main()
