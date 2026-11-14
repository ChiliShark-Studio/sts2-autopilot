"""抽牌药水的使用时机: 手牌差的时候用, 不是血量低的时候.

真机复盘: 瓶装潜能 (抽 5 张) 一直留到 6 血才喝, 那时已经救不回来。
抽牌的价值取决于"手里有没有好牌可打", 而不是血量比例。
"""

from __future__ import annotations

import unittest

from brain.actions import UsePotion
from brain.combat.search import CombatSearchTool
from brain.contracts import Decision, DecisionKind, DecisionRequest, Source
from brain.model import GameState

WHIRL = {"name": "旋风斩", "cost": "3", "damage": 8, "hits": 1,
         "target_type": "AllEnemies", "can_play": True}
DENY = {"name": "疑虑", "cost": "0", "type": "Status", "can_play": False,
        "target_type": "Self", "description": "不能被打出。"}
STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}
DRAW_POTION = {"id": "POTION_DRAW", "name": "瓶装潜能", "description": "抽5张牌。",
               "usable": True, "slot": 0}


def state(*, hand, energy=1, hp=70, incoming=12):
    return {
        "state_type": "boss",
        "battle": {
            "round": 3, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "女王", "hp": 400, "max_hp": 400,
                         "status": [], "intents": [{"type": "Attack", "damage": incoming,
                                                    "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": 84, "energy": energy, "status": [],
                   "hand": hand, "potions": [dict(DRAW_POTION)]},
    }


class _StubAdvisor:
    """假的药水顾问: 被问到就同意喝第 0 个槽位."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_decline = ""

    def advise(self, value, potions):
        self.calls += 1
        return Decision(action=UsePotion(potions[0].slot, None), reason="抽牌救手牌",
                        source=Source.LLM, tool="potion_advisor")


def decide(raw):
    tool = CombatSearchTool(advisor=_StubAdvisor())
    return tool.decide(DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw)))


class HandQualityTests(unittest.TestCase):
    def test_draw_potion_used_when_hand_is_stuck(self):
        # 能量 1, 手里只有打不起的旋风斩和不能打的疑虑 -> 该抽牌.
        decision = decide(state(hand=[dict(WHIRL, index=0), dict(DENY, index=1)], energy=1))
        self.assertEqual(decision.action.kind, "use_potion")

    def test_draw_potion_saved_when_hand_is_fine(self):
        # 手里有能打的打击, 能量够, 血量健康 -> 不该喝.
        hand = [dict(STRIKE, index=0), dict(STRIKE, index=1), dict(STRIKE, index=2)]
        decision = decide(state(hand=hand, energy=3, hp=70))
        self.assertNotEqual(decision.action.kind, "use_potion")


if __name__ == "__main__":
    unittest.main()
