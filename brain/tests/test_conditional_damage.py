"""条件伤害牌: 欺凌(按易伤层数)、灰烬打击(按消耗数).

这类牌桥接会按"当前条件"算出 0, 直接用牌面 damage 会把它们当成 0 伤牌.
"""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.combat.search import _Sim, decide_state

BULLY = {
    "index": 0, "id": "BULLY", "name": "欺凌", "type": "Attack", "cost": "0",
    "damage": 0, "hits": 1,
    "description": "造成4点伤害。 该敌人身上每有一层易伤就额外造成2点伤害。",
    "target_type": "AnyEnemy", "can_play": True,
}

ASHEN = {
    "index": 1, "id": "ASHEN_STRIKE", "name": "灰烬打击", "type": "Attack", "cost": "1",
    "damage": 0, "hits": 1,
    "description": "造成6点伤害。 你的消耗牌堆中每有一张牌，伤害增加3。",
    "target_type": "AnyEnemy", "can_play": True,
}

TREMBLE = {
    "index": 2, "id": "TREMBLE", "name": "战栗", "type": "Skill", "cost": "1",
    "vulnerable": 3,
    "description": "给予3层易伤。 消耗。", "target_type": "AnyEnemy", "can_play": True,
}


def state(*, hand, enemy_vuln=0, enemy_hp=200, energy=3, hp=60, incoming=10):
    return {
        "state_type": "monster",
        "battle": {
            "round": 2, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E_0", "name": "测试怪", "hp": enemy_hp,
                         "max_hp": enemy_hp,
                         "status": [{"id": "VULNERABLE_POWER", "name": "易伤",
                                     "amount": enemy_vuln}] if enemy_vuln else [],
                         "intents": [{"type": "Attack", "damage": incoming, "hits": 1,
                                      "label": str(incoming),
                                      "description": f"这个敌人将要攻击造成{incoming}点伤害。"}]}],
        },
        "player": {"hp": hp, "max_hp": 80, "energy": energy, "hand": hand, "potions": []},
    }


class ParseTests(unittest.TestCase):
    def test_bully_keeps_base_damage_and_scaling(self):
        parsed = parse_card(BULLY)
        self.assertEqual(parsed["damage"], 4)      # 桥接给 0, 描述里是 4
        self.assertEqual(parsed["per_vulnerable"], 2)

    def test_ashen_strike_keeps_scaling(self):
        parsed = parse_card(ASHEN)
        self.assertEqual(parsed["damage"], 6)
        self.assertEqual(parsed["per_exhaust"], 3)

    def test_tremble_is_self_exhaust(self):
        self.assertTrue(parse_card(TREMBLE)["exhaust"])


class DamageTests(unittest.TestCase):
    def test_bully_scales_with_vulnerable(self):
        parsed = GameState.parse(state(hand=[dict(BULLY)], enemy_vuln=2))
        sim = _Sim.build(parsed.player, parsed.battle)
        enemy = sim.enemies[0]
        self.assertTrue(sim.play(0, 0))
        # (4 + 2*2) * 1.5 = 12
        self.assertEqual(enemy.hp, 200 - 12)

    def test_bully_is_weak_without_vulnerable(self):
        parsed = GameState.parse(state(hand=[dict(BULLY)], enemy_vuln=0))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 4)

    def test_ashen_strike_scales_with_exhausted_cards(self):
        parsed = GameState.parse(state(hand=[dict(ASHEN), dict(TREMBLE), dict(TREMBLE)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(1, 0)                 # 战栗: 消耗 + 3 易伤
        self.assertEqual(sim.exhausted, 1)
        enemy = sim.enemies[0]
        before = enemy.hp
        sim.play(0, 0)                 # 灰烬打击: (6 + 3*1) * 1.5 = 13
        self.assertEqual(enemy.hp, before - 13)


SHRUNK_BULLY = {
    "index": 0, "id": "BULLY", "name": "欺凌", "type": "Attack", "cost": "0",
    "damage": 0, "hits": 1,
    # 缩小时桥接把描述算成 2, 基础伤害仍是 4.
    "description": "造成2点伤害。 该敌人身上每有一层易伤就额外造成2点伤害。",
    "target_type": "AnyEnemy", "can_play": True,
}

DISMANTLE = {
    "index": 0, "id": "DISMANTLE", "name": "拆卸", "type": "Attack", "cost": "1",
    "damage": 8, "hits": 1,
    "description": "造成8点伤害。 如果该敌人有易伤状态，则攻击两次。",
    "target_type": "AnyEnemy", "can_play": True,
}


class ShrinkTests(unittest.TestCase):
    """缩小是标记型能力 (层数 -1), 攻击伤害 -30%."""

    def _sim(self, hand):
        raw = state(hand=hand)
        raw["player"]["status"] = [{"id": "SHRINK_POWER", "name": "缩小", "amount": -1,
                                    "description": "缩小甲虫存活时，你的攻击伤害减少30%。"}]
        parsed = GameState.parse(raw)
        return _Sim.build(parsed.player, parsed.battle)

    def test_shrink_flag_is_detected_with_negative_amount(self):
        sim = self._sim([{"index": 0, "name": "打击", "cost": "1", "damage": 6,
                          "hits": 1, "target_type": "AnyEnemy"}])
        self.assertTrue(sim.shrink)

    def test_shrink_reduces_damage(self):
        sim = self._sim([{"index": 0, "name": "打击", "cost": "1", "damage": 6,
                          "hits": 1, "target_type": "AnyEnemy"}])
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 4)   # int(6 * 0.7)

    def test_shrunk_description_does_not_double_count(self):
        # 描述已经被缩小算成 2; 若再乘 0.7 会得到 1, 实际应为 2.
        sim = self._sim([dict(SHRUNK_BULLY)])
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 2)


class ConditionalHitsTests(unittest.TestCase):
    """拆卸: 目标有易伤时攻击两次."""

    def test_double_hit_when_target_is_vulnerable(self):
        parsed = GameState.parse(state(hand=[dict(DISMANTLE)], enemy_vuln=1))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 24)   # 8*1.5 两段

    def test_single_hit_without_vulnerable(self):
        parsed = GameState.parse(state(hand=[dict(DISMANTLE)], enemy_vuln=0))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 200 - 8)


class DecisionTests(unittest.TestCase):
    def test_bully_is_played_for_free(self):
        decision = decide_state(state(hand=[dict(BULLY)], enemy_vuln=3, energy=0))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "欺凌")

    def test_bully_prefers_target_with_vulnerable(self):
        raw = state(hand=[dict(BULLY)], enemy_vuln=0)
        raw["battle"]["enemies"].append({
            "entity_id": "E_1", "name": "易伤怪", "hp": 200, "max_hp": 200,
            "status": [{"id": "VULNERABLE_POWER", "name": "易伤", "amount": 3}],
            "intents": [{"type": "Attack", "damage": 5, "hits": 1, "label": "5",
                         "description": "这个敌人将要攻击造成5点伤害。"}]})
        decision = decide_state(raw)
        self.assertEqual(decision.action.target, "E_1")


if __name__ == "__main__":
    unittest.main()
