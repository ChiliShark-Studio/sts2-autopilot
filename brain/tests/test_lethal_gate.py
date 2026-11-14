"""致命回合不允许打能力牌.

复盘: 女王战三次出现模型顾问的"敌人下回合 39 伤不致命"——当时只有 38 血, 39 > 38。
这类硬判断由代码兜底: 本回合必死时先活命, 不投资能力牌、不付血换节奏。
"""

from __future__ import annotations

import unittest

from brain.actions import PlayCard
from brain.combat.search import CombatSearchTool
from brain.contracts import Decision, DecisionKind, DecisionRequest, Source
from brain.model import GameState

POWER = {"name": "未知能力", "type": "Power", "cost": "1", "can_play": True,
         "target_type": "Self", "damage": 0, "block": 0,
         "description": "每当你打出一张攻击牌，抽一张牌。"}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}


def state(*, hp=38, incoming=39):
    return {
        "state_type": "boss",
        "battle": {
            "round": 4, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "QUEEN_0", "name": "女王", "hp": 300, "max_hp": 400,
                         "status": [], "intents": [{"type": "Attack", "damage": incoming,
                                                    "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": 84, "energy": 3, "status": [],
                   "hand": [dict(POWER, index=0), dict(STRIKE, index=1)], "potions": []},
    }


class _AlwaysPlayPower:
    """假的顾问: 永远建议打能力牌."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_verdict = "unavailable"
        self.last_note = ""

    def candidates(self, value):
        return [card for card in value.battle.hand if card.type.lower() == "power"]

    def advise(self, value, cards):
        self.calls += 1
        return Decision(action=PlayCard(cards[0], None), reason="顾问建议打",
                        source=Source.LLM, tool="power_advisor")


def decide(raw, advisor):
    tool = CombatSearchTool(power=advisor)
    return tool.decide(DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw)))


class LethalGateTests(unittest.TestCase):
    def test_advisor_is_not_asked_on_a_lethal_turn(self):
        advisor = _AlwaysPlayPower()
        decide(state(hp=38, incoming=39), advisor)
        self.assertEqual(advisor.calls, 0)

    def test_advisor_still_asked_when_plan_survives(self):
        advisor = _AlwaysPlayPower()
        decide(state(hp=80, incoming=10), advisor)
        self.assertEqual(advisor.calls, 1)


if __name__ == "__main__":
    unittest.main()
