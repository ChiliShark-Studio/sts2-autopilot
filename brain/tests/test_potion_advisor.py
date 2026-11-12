"""战斗内药水顾问测试: 只有无法模拟的药水才会交给模型判断."""

from __future__ import annotations

import unittest

from brain.contracts import Decision, DecisionKind, DecisionRequest, Source
from brain.actions import UsePotion
from brain.model import GameState
from brain.tools.combat_search import CombatSearchTool


def state(*, hp=8, max_hp=80, energy=3, enemy_hp=40, incoming=15, hand=None, potions=None) -> dict:
    return {
        "state_type": "monster",
        "battle": {
            "round": 2, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E_0", "name": "测试怪", "hp": enemy_hp, "max_hp": enemy_hp,
                         "intents": [{"type": "Attack", "damage": incoming, "hits": 1,
                                      "label": str(incoming),
                                      "description": f"这个敌人将要攻击造成{incoming}点伤害。"}]}],
        },
        "player": {
            "hp": hp, "max_hp": max_hp, "energy": energy,
            "hand": hand if hand is not None else [
                {"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1}],
            "potions": potions or [],
        },
    }


def colorless_potion(slot=0):
    return {"slot": slot, "id": "COLORLESS_POTION", "name": "无色药水",
            "description": "从3张随机无色牌中选择1张加入你的手牌。这张牌在本回合可以免费打出。",
            "target_type": "AnyPlayer", "can_use_in_combat": True}


def block_potion(slot=0):
    return {"slot": slot, "id": "BLOCK_POTION", "name": "格挡药水",
            "description": "获得12点格挡。", "target_type": "Self",
            "can_use_in_combat": True}


class _Advisor:
    def __init__(self, decision=None):
        self.calls = []
        self.decision = decision

    def advise(self, state, potions):  # noqa: ANN001
        self.calls.append([p.name for p in potions])
        return self.decision


def request(payload) -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(payload))


class AdvisorRoutingTests(unittest.TestCase):
    def test_unknown_potion_asks_model_when_plan_would_die(self):
        advisor = _Advisor(Decision(action=UsePotion(0), reason="赌一张牌",
                                    source=Source.LLM, tool="potion_advisor"))
        tool = CombatSearchTool(advisor=advisor)
        decision = tool.decide(request(state(potions=[colorless_potion()])))
        self.assertEqual(len(advisor.calls), 1)
        self.assertEqual(advisor.calls[0], ["无色药水"])
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIs(decision.source, Source.LLM)

    def test_known_potion_is_handled_by_search(self):
        advisor = _Advisor(Decision(action=UsePotion(0), reason="不该被调用",
                                    source=Source.LLM, tool="potion_advisor"))
        tool = CombatSearchTool(advisor=advisor)
        decision = tool.decide(request(state(potions=[block_potion()])))
        self.assertEqual(advisor.calls, [])
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("保命", decision.reason)

    def test_no_advice_when_plan_survives(self):
        advisor = _Advisor(Decision(action=UsePotion(0), reason="不该被调用",
                                    source=Source.LLM, tool="potion_advisor"))
        tool = CombatSearchTool(advisor=advisor)
        decision = tool.decide(request(state(hp=70, incoming=1, potions=[colorless_potion()])))
        self.assertEqual(advisor.calls, [])
        self.assertNotEqual(decision.action.kind, "use_potion")

    def test_declining_keeps_search_decision(self):
        advisor = _Advisor(None)
        tool = CombatSearchTool(advisor=advisor)
        decision = tool.decide(request(state(potions=[colorless_potion()])))
        self.assertEqual(len(advisor.calls), 1)
        self.assertNotEqual(decision.action.kind, "use_potion")

    def test_same_frame_does_not_ask_twice(self):
        advisor = _Advisor(None)
        tool = CombatSearchTool(advisor=advisor)
        raw = state(potions=[colorless_potion()])
        for _ in range(3):
            tool.decide(request(raw))
        self.assertEqual(len(advisor.calls), 1)

    def test_advisor_disabled_without_client(self):
        tool = CombatSearchTool()
        decision = tool.decide(request(state(potions=[colorless_potion()])))
        self.assertNotEqual(decision.action.kind, "use_potion")


class _DecliningAdvisor:
    """模型选择不使用, 但留下原因."""

    def __init__(self):
        self.last_decline = "模型选择不使用：格挡不够"

    def advise(self, state, potions):  # noqa: ANN001
        return None


class DeclineLoggingTests(unittest.TestCase):
    def test_decline_reason_is_recorded_on_decision(self):
        tool = CombatSearchTool(advisor=_DecliningAdvisor())
        decision = tool.decide(request(state(potions=[colorless_potion()])))
        self.assertEqual((decision.extra or {}).get("potion_advice"),
                         "模型选择不使用：格挡不够")


if __name__ == "__main__":
    unittest.main()
