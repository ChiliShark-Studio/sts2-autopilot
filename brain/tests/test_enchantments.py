"""附魔 (锋利/浸墨): 加成在 keywords 里, 结构化 damage 不含, 必须补上."""

from __future__ import annotations

import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.combat.search import _Sim

# 真实数据: run-20261112-223946 里带「锋利」的重锤.
BLUDGEON = {
    "id": "BLUDGEON", "name": "重锤", "type": "Attack", "cost": "3",
    "damage": 32, "damage_base": 32, "hits": 1,
    "description": "造成34点伤害。", "is_upgraded": False,
    "keywords": [{"name": "锋利", "description": "这张牌上的伤害值增加2点。"}],
}

INKED_STRIKE = {
    "id": "STRIKE_IRONCLAD", "name": "打击", "type": "Attack", "cost": "1",
    "damage": 6, "damage_base": 6, "hits": 1,
    "description": "造成8点伤害。", "is_upgraded": False,
    "keywords": [{"name": "浸墨", "description": "卡牌额外造成2点伤害，且施加1层虚弱。"}],
}

PLAIN = {
    "id": "BLUDGEON", "name": "重锤", "type": "Attack", "cost": "3",
    "damage": 32, "damage_base": 32, "hits": 1,
    "description": "造成32点伤害。", "is_upgraded": False, "keywords": [],
}

ENCHANTED_DEFEND = {
    "id": "DEFEND_IRONCLAD", "name": "防御", "type": "Skill", "cost": "1",
    "damage": 0, "block": 8, "hits": 1,
    "description": "获得10点格挡。", "is_upgraded": False,
    "keywords": [{"name": "坚固", "description": "这张牌上的格挡值增加2点。"}],
}


class EnchantParseTests(unittest.TestCase):
    def test_sharp_enchant_adds_damage(self):
        # 32 + 锋利2 = 34, 与游戏描述一致.
        self.assertEqual(parse_card(BLUDGEON)["damage"], 34)

    def test_without_enchant_keeps_base(self):
        self.assertEqual(parse_card(PLAIN)["damage"], 32)

    def test_inked_enchant_adds_damage(self):
        self.assertEqual(parse_card(INKED_STRIKE)["damage"], 8)

    def test_enchant_can_add_block(self):
        self.assertEqual(parse_card(ENCHANTED_DEFEND)["block"], 10)


class DamageTests(unittest.TestCase):
    def _hit(self, card: dict, enemy_hp: int = 200) -> int:
        raw = {
            "state_type": "monster",
            "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                       "enemies": [{"entity_id": "E_0", "name": "木桩", "hp": enemy_hp,
                                    "max_hp": enemy_hp,
                                    "intents": [{"type": "Attack", "damage": 0, "hits": 1,
                                                 "label": "0", "description": ""}]}]},
            "player": {"hp": 50, "max_hp": 80, "energy": 3,
                       "hand": [dict(card, index=0)], "potions": []},
        }
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, 0)
        return enemy_hp - sim.enemies[0].hp

    def test_damage_includes_enchant(self):
        self.assertEqual(self._hit(BLUDGEON), 34)

    def test_damage_without_enchant(self):
        self.assertEqual(self._hit(PLAIN), 32)


if __name__ == "__main__":
    unittest.main()
