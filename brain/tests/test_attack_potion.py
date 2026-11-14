"""攻击药水的使用时机: 补关键伤害, 与血量无关.

复盘: 攻击药水只在血量低于六成时才被考虑, 但它的价值是"差一点就能打死"——
敌人剩 12 血、本回合只能打 6 点, 一瓶 12 伤药水就是斩杀。
"""

from __future__ import annotations

import unittest

from brain.actions import UsePotion
from brain.combat.search import CombatSearchTool
from brain.contracts import Decision, DecisionKind, DecisionRequest, Source
from brain.model import GameState

STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}
DAMAGE_POTION = {"id": "POTION_FIRE", "name": "火焰药水", "description": "造成12点伤害。",
                 "usable": True, "slot": 0}
RANDOM_ATTACK_POTION = {"id": "POTION_ATK", "name": "攻击药水",
                        "description": "获得一张随机攻击牌。", "usable": True, "slot": 0}


def state(*, enemy_hp=12, hp=70, potion=None, hand=None):
    return {
        "state_type": "boss",
        "battle": {
            "round": 3, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": "女王", "hp": enemy_hp, "max_hp": 400,
                         "status": [], "intents": [{"type": "Attack", "damage": 15, "hits": 1}]}],
        },
        "player": {"hp": hp, "max_hp": 84, "energy": 3, "status": [],
                   "hand": hand if hand is not None else [dict(STRIKE, index=0)],
                   "potions": [dict(potion or DAMAGE_POTION)]},
    }


class _Advisor:
    def __init__(self) -> None:
        self.calls = 0
        self.last_decline = ""

    def advise(self, value, potions):
        self.calls += 1
        return Decision(action=UsePotion(potions[0].slot, None), reason="补关键伤害",
                        source=Source.LLM, tool="potion_advisor")


def decide(raw, advisor=None):
    tool = CombatSearchTool(advisor=advisor or _Advisor())
    return tool.decide(DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw)))


class AttackPotionTests(unittest.TestCase):
    def test_damage_potion_used_to_finish_at_full_hp(self):
        # 敌人 12 血, 手里一张打击只能打 6: 药水正好补掉, 满血也该用.
        decision = decide(state(enemy_hp=12, hp=80))
        self.assertEqual(decision.action.kind, "use_potion")
        # 能直接斩杀时走"斩杀"分支, 同样算补关键伤害.
        self.assertIn("药水", decision.reason)

    def test_damage_potion_saved_when_not_needed(self):
        # 敌人 400 血, 一瓶 12 伤改变不了什么, 满血不该喝.
        decision = decide(state(enemy_hp=400, hp=80))
        self.assertNotEqual(decision.action.kind, "use_potion")

    def test_unknown_attack_potion_asked_when_enemy_is_near_death(self):
        advisor = _Advisor()
        decide(state(enemy_hp=10, hp=80, potion=RANDOM_ATTACK_POTION), advisor)
        self.assertEqual(advisor.calls, 1)


if __name__ == "__main__":
    unittest.main()
