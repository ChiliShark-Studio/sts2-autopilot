"""游戏自己算的伤害 (damage_by_target): 第一张牌直接用, 后续牌仍用本地模型.

桥接对每张攻击牌、每个存活敌人调用官方预览接口
(CardModel.UpdateDynamicVarPreview), 结果含力量/易伤/虚弱/缩小/附魔/遗物.
"""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.tools.combat_search import _Sim, decide_state

STRIKE = {
    "index": 0, "id": "STRIKE_IRONCLAD", "name": "打击", "type": "Attack", "cost": "1",
    "damage": 6, "damage_base": 6, "hits": 1,
    "description": "造成6点伤害。", "target_type": "AnyEnemy", "can_play": True,
    # 游戏算出来是 9 (有力量/易伤等修正).
    "damage_by_target": {"E_0": 9},
}


def state(*, hand, enemy_hp=200, hp=50, energy=3, status=None, vuln=0):
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E_0", "name": "木桩", "hp": enemy_hp,
                         "max_hp": enemy_hp,
                         "status": [{"id": "VULNERABLE_POWER", "name": "易伤",
                                     "amount": vuln}] if vuln else [],
                         "intents": [{"type": "Attack", "damage": 0, "hits": 1,
                                      "label": "0", "description": ""}]}],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "hand": hand,
                   "status": status or [], "potions": []},
    }


class ParseTests(unittest.TestCase):
    def test_damage_by_target_is_kept(self):
        parsed = parse_card(STRIKE)
        self.assertEqual(parsed["damage_by_target"], {"E_0": 9})

    def test_missing_field_is_empty(self):
        card = {k: v for k, v in STRIKE.items() if k != "damage_by_target"}
        self.assertEqual(parse_card(card)["damage_by_target"], {})


class FirstCardTests(unittest.TestCase):
    def test_game_number_is_used_for_first_card(self):
        parsed = GameState.parse(state(hand=[dict(STRIKE)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 9)   # 用游戏的 9, 不是本地算的 6

    def test_falls_back_when_bridge_is_old(self):
        card = {k: v for k, v in STRIKE.items() if k != "damage_by_target"}
        parsed = GameState.parse(state(hand=[card]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 6)   # 没有该字段时用牌面

    def test_later_cards_use_local_model(self):
        # 第一张用游戏的 9; 第二张牌的预览值是基于"出牌前"的状态, 不能再用.
        hand = [dict(STRIKE, index=0), dict(STRIKE, index=1)]
        parsed = GameState.parse(state(hand=hand, enemy_hp=200))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        sim.play(0, 0)
        # 第二刀走本地模型: 牌面 6 + 无力量 = 6
        self.assertEqual(sim.enemies[0].hp, 200 - 9 - 6)


class ShrinkTests(unittest.TestCase):
    def test_game_number_wins_over_local_shrink_math(self):
        # 缩小在场时本地会算成 int(6*0.7)=4; 游戏的 9 优先.
        parsed = GameState.parse(state(hand=[dict(STRIKE)],
                                       status=[{"id": "SHRINK_POWER", "name": "缩小",
                                                "amount": -1}]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 9)


class DecisionTests(unittest.TestCase):
    def test_lethal_uses_game_damage(self):
        # 敌人剩 9 血: 游戏给的 9 正好斩杀.
        parsed = GameState.parse(state(hand=[dict(STRIKE)], enemy_hp=9))
        decision = decide_state(state(hand=[dict(STRIKE)], enemy_hp=9))
        self.assertIn("斩杀", decision.reason)


if __name__ == "__main__":
    unittest.main()
