"""卡牌解析体检: 描述有内容但解析没接住的牌要能被发现.

今天连续踩到: 残酷、覆甲、祭品抽牌、胆小、痛殴、痛击易伤——
都是"桥接只给描述, 解析没覆盖 → 牌变白板"。体检就是为了下次不必靠人眼发现。
"""

from __future__ import annotations

import unittest

from brain.model import GameState
from brain.strategy.card_audit import audit, audit_card


def card(**kwargs):
    base = {"name": "测试牌", "type": "Attack", "cost": "1", "damage": 0, "block": 0,
            "hits": 1, "description": "", "can_play": True}
    base.update(kwargs)
    state = {"state_type": "monster",
             "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                        "enemies": [{"entity_id": "E0", "name": "怪", "hp": 40, "max_hp": 40,
                                     "status": [], "intents": [{"type": "Attack", "damage": 5,
                                                                "hits": 1}]}]},
             "player": {"hp": 60, "max_hp": 80, "energy": 3, "status": [],
                        "hand": [base]}}
    return GameState.parse(state).battle.hand[0]


class AuditTests(unittest.TestCase):
    def test_detects_effect_text_without_parsed_field(self):
        # 纯文本效果 (例如每当你打出攻击牌抽一张) 解析不出来, 应当被标记.
        warning = audit_card(card(name="未知能力", type="Power",
                                  description="每当你打出一张攻击牌，抽一张牌。"))
        self.assertIsNotNone(warning)
        self.assertIn("抽牌", " ".join(warning.hints))

    def test_fully_parsed_card_is_not_flagged(self):
        self.assertIsNone(audit_card(card(name="打击", damage=6,
                                          description="造成6点伤害。")))

    def test_vulnerable_from_description_is_not_flagged(self):
        # 桥接给 0 但描述有 2 层, 解析已经按描述补上 → 不该再报.
        self.assertIsNone(audit_card(card(name="痛击", damage=8,
                                          description="造成8点伤害。给予2层易伤。",
                                          vulnerable=0)))

    def test_absorb_attack_counts_as_exhaust(self):
        # 痛殴消耗一张攻击牌把伤害加进来, 这条已经建模, 不该被当成"未标记".
        self.assertIsNone(audit_card(card(
            name="痛殴", damage=4, hits=2,
            description="造成4点伤害两次。消耗你的手牌中随机一张攻击牌，并将它的伤害添加给这张牌。")))

    def test_deduplicates_by_name(self):
        cards = [card(name="测试牌", description="抽一张牌。"),
                 card(name="测试牌", description="抽一张牌。")]
        self.assertEqual(len(audit(cards)), 1)


if __name__ == "__main__":
    unittest.main()
