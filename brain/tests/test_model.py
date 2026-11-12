"""模型解析测试.

覆盖两条重要路径:

* ``captures/combat-001.json`` 来自 Mod 补丁之前, 只有中文描述,
  用真实数据确认文本解析兜底仍然有效.
* 使用带结构化数值字段的状态, 确认优先读取结构化值.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brain.model import GameState, Screen

CAPTURE = Path(__file__).resolve().parents[2] / "captures" / "combat-001.json"


def load_capture() -> dict:
    return json.loads(CAPTURE.read_text(encoding="utf-8-sig"))


class ModelTests(unittest.TestCase):
    def test_parses_real_combat_capture(self):
        if not CAPTURE.exists():
            self.skipTest(f"missing capture: {CAPTURE}")
        state = GameState.parse(load_capture())

        self.assertIs(state.screen, Screen.COMBAT)
        self.assertIsNotNone(state.battle)
        assert state.battle is not None
        self.assertTrue(state.battle.player_turn)
        self.assertGreater(len(state.battle.hand), 0)

    def test_text_fallback_reads_damage_from_description(self):
        """采集状态中没有数值字段, 解析器仍应得到伤害 6."""
        state = GameState.parse(load_capture())
        assert state.battle is not None
        strike = state.battle.hand[0]
        self.assertEqual(strike.name, "打击")
        self.assertEqual(strike.damage, 6)
        self.assertEqual(strike.cost, 1)

    def test_structured_fields_win_over_text(self):
        payload = {
            "state_type": "monster",
            "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                       "enemies": [{"entity_id": "X_0", "hp": 10,
                                    "intents": [{"type": "Attack", "damage": 7, "hits": 2}]}]},
            "player": {"hp": 50, "max_hp": 80, "energy": 3,
                       "hand": [{"index": 0, "name": "测试", "cost": "1", "damage": 9, "block": 0,
                                 "hits": 2, "description": "造成1点伤害。"}]},
        }
        state = GameState.parse(payload)
        assert state.battle is not None
        card = state.battle.hand[0]
        self.assertEqual(card.damage, 9)  # 使用结构化数值, 而非文本中的 1
        self.assertEqual(card.hits, 2)
        self.assertEqual(state.battle.incoming, 14)  # 每次伤害 7, 共 2 次

    def test_star_cost_cards_are_not_playable(self):
        """储君卡牌消耗星星, 当前未建模该资源, 不应尝试打出."""
        state = GameState.parse({
            "state_type": "monster",
            "battle": {"turn": "player", "ready_for_action": True, "enemies": []},
            "player": {"hp": 50, "max_hp": 80, "energy": 3, "hand": [
                {"index": 0, "name": "陨星", "cost": "0", "star_cost": "2", "damage": 8},
            ]},
        })
        assert state.battle is not None
        self.assertFalse(state.battle.hand[0].can_play)

    def test_defaults_are_total(self):
        state = GameState.parse({})
        self.assertIs(state.screen, Screen.UNKNOWN)
        self.assertEqual(state.player.hp, 0)
        self.assertIsNone(state.battle)
        self.assertFalse(state.is_over)

    def test_menu_is_not_a_decision_screen(self):
        state = GameState.parse({"state_type": "menu", "menu_screen": "main", "options": ["continue"]})
        self.assertIs(state.screen, Screen.MENU)
        self.assertFalse(state.in_combat)


if __name__ == "__main__":
    unittest.main()
