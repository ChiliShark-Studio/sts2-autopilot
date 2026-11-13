"""用生命换节奏的牌 (祭品 / 放血).

单回合搜索没法模拟"抽到的那几张牌", 所以祭品 (失去 6 生命, 获得 2 能量, 抽 3 张)
在模型里只剩"掉血"这一半, 永远评不过一张打击。

真机证据 (2026-11-14 第一幕第 12 层): 32/80 血、手里有祭品与 3 张攻击牌,
搜索选择了耸肩无视, 一次都没打祭品。规则改为: 血量还够且本回合打不死敌人时,
先把节奏牌打出去。
"""

from __future__ import annotations

import unittest

from brain.combat.search import decide_state

OFFERING = {"index": 0, "id": "OFFERING", "name": "祭品", "type": "Skill", "cost": "0",
            "target_type": "Self", "can_play": True, "damage": 0, "block": 0, "hits": 1,
            "description": "失去6点生命。 获得[ironclad_energy_icon.png][ironclad_energy_icon.png]。 抽3张牌。 消耗。"}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1, "target_type": "AnyEnemy",
          "can_play": True}


def state(*, hp, max_hp=80, enemy_hp=60, hand=None, deck=None):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "怪", "hp": enemy_hp, "max_hp": enemy_hp,
                         "status": [], "intents": [{"type": "Attack", "damage": 10, "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": max_hp, "energy": 3, "status": [],
                   "deck": deck or [],
                   "hand": hand if hand is not None else
                   [dict(OFFERING, index=0), dict(STRIKE, index=1), dict(STRIKE, index=2)]},
    }


class TempoCardTests(unittest.TestCase):
    def test_plays_offering_when_healthy(self):
        decision = decide_state(state(hp=60))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "祭品")

    def test_plays_offering_at_the_recorded_hp(self):
        # 真机那一手的血量: 32/80.
        decision = decide_state(state(hp=32))
        self.assertEqual(decision.action.card.name, "祭品")

    def test_does_not_pay_hp_when_low(self):
        decision = decide_state(state(hp=20))
        self.assertNotEqual(getattr(decision.action.card, "name", ""), "祭品")

    def test_still_takes_the_lethal_line(self):
        decision = decide_state(state(hp=60, enemy_hp=6))
        self.assertIn("斩杀", decision.reason)


class DrawValueTests(unittest.TestCase):
    """抽牌的价值按剩余牌库算, 不是拍一个常数."""

    def _sim(self, deck):
        from brain.combat.search import _Sim
        from brain.model import GameState

        parsed = GameState.parse(state(hp=60, deck=deck))
        return _Sim.build(parsed.player, parsed.battle)

    def test_draw_value_follows_the_deck(self):
        strike = {"name": "打击", "cost": "1", "damage": 6, "hits": 1, "target_type": "AnyEnemy"}
        defend = {"name": "防御", "cost": "1", "block": 5, "target_type": "Self"}
        attacks = self._sim([dict(strike) for _ in range(10)])
        blocks = self._sim([dict(defend) for _ in range(10)])
        self.assertGreater(attacks.expected_draw_value(), blocks.expected_draw_value())

    def test_each_draw_adds_the_expected_value(self):
        from brain.combat.search import _Sim
        from brain.model import GameState

        deck = [dict(STRIKE) for _ in range(10)]
        parsed = GameState.parse(state(hp=60, deck=deck, hand=[dict(OFFERING, index=0)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        before = sim.future_value
        sim.play(0, None)
        self.assertAlmostEqual(sim.future_value - before, 3 * sim.expected_draw_value(), places=6)
        self.assertGreater(sim.expected_draw_value(), 0)


if __name__ == "__main__":
    unittest.main()
