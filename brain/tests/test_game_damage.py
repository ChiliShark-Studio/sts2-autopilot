"""桥接的 ``damage_by_target`` 不完整: 不参与伤害计算.

真机证据 (2026-11-14 暗港精英战, data/combat/underdocks-colony-20261114.jsonl):
玩家带 3 点力量时, 铁斩波牌面 5、卡面描述"造成8点伤害", 而 ``damage_by_target``
仍是 5; 打击 6 / 描述 9, 熔融之拳 10 / 描述 13, 一律少掉力量那一份.
把该字段当伤害会让模拟器每张攻击少算力量: 用 tools/replay_fight.py 回放录像时,
6 处对不上全都正好差 3.

因此搜索一律用本地模型 (含力量/易伤/虚弱/缩小/缓慢), 字段只留在状态里备查.
"""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.combat.search import _Sim, decide_state

STRIKE = {
    "index": 0, "id": "STRIKE_IRONCLAD", "name": "打击", "type": "Attack", "cost": "1",
    "damage": 6, "damage_base": 6, "hits": 1,
    "description": "造成6点伤害。", "target_type": "AnyEnemy", "can_play": True,
    # 桥接给的数字不含玩家力量: 这里故意写成 6, 模拟器不应采用.
    "damage_by_target": {"E_0": 6},
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


STRENGTH_3 = [{"id": "STRENGTH_POWER", "name": "力量", "amount": 3}]


class ParseTests(unittest.TestCase):
    def test_field_is_kept_for_diagnostics(self):
        self.assertEqual(parse_card(STRIKE)["damage_by_target"], {"E_0": 6})

    def test_missing_field_is_empty(self):
        card = {k: v for k, v in STRIKE.items() if k != "damage_by_target"}
        self.assertEqual(parse_card(card)["damage_by_target"], {})


class DamageTests(unittest.TestCase):
    def test_strength_is_applied_even_when_bridge_number_is_lower(self):
        # 桥接的 6 不含力量; 本地模型 6+3=9 才是游戏实际伤害.
        parsed = GameState.parse(state(hand=[dict(STRIKE)], status=STRENGTH_3))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 9)

    def test_missing_field_still_uses_local_model(self):
        card = {k: v for k, v in STRIKE.items() if k != "damage_by_target"}
        parsed = GameState.parse(state(hand=[card], status=STRENGTH_3))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 9)

    def test_vulnerable_is_applied_after_strength(self):
        parsed = GameState.parse(state(hand=[dict(STRIKE)], status=STRENGTH_3, vuln=2))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - int(9 * 1.5))

    def test_shrink_reduces_local_damage(self):
        parsed = GameState.parse(state(hand=[dict(STRIKE)],
                                       status=[{"id": "SHRINK_POWER", "name": "缩小",
                                                "amount": -1}]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 4)   # int(6 * 0.7)


class DecisionTests(unittest.TestCase):
    def test_lethal_uses_local_damage_with_strength(self):
        # 敌人剩 9 血, 6 点打击加 3 点力量正好斩杀.
        raw = state(hand=[dict(STRIKE)], enemy_hp=9, status=STRENGTH_3)
        decision = decide_state(raw)
        self.assertIn("斩杀", decision.reason)


if __name__ == "__main__":
    unittest.main()
