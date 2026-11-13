"""节奏牌顾问: 祭品/放血要不要现在打, 交给模型判断.

背景: 这类牌一半价值在"抽到的牌", 单回合搜索只能按牌库平均值估算, 打与不打的
分差很小。因此边界情况问模型; 模型说留着就不再兜底, 没有模型时才走"血量够就打"。
"""

from __future__ import annotations

import unittest

from brain.combat.search import CombatSearchTool
from brain.combat.tempo_advisor import TempoAdvisor
from brain.contracts import DecisionKind, DecisionRequest, Source
from brain.llm import make_mock_client
from brain.model import GameState
from brain.tests.test_tempo_cards import OFFERING, STRIKE, state


def request(raw):
    return DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw))


class TempoAdvisorTests(unittest.TestCase):
    def test_model_can_choose_to_play_it(self):
        client = make_mock_client({})
        client.json_chat = lambda system, user: {"choice": 0, "reason": "多打一轮"}
        tool = CombatSearchTool(tempo=TempoAdvisor(client))
        decision = tool.decide(request(state(hp=60)))
        self.assertIs(decision.source, Source.LLM)
        self.assertEqual(decision.action.card.name, "祭品")
        self.assertIn("多打一轮", decision.reason)

    def test_model_can_keep_it(self):
        client = make_mock_client({})
        client.json_chat = lambda system, user: {"choice": -1, "reason": "先看敌人下回合"}
        tool = CombatSearchTool(tempo=TempoAdvisor(client))
        decision = tool.decide(request(state(hp=60)))
        # 模型说留着: 不能再用"血量够就打"的规则兜底.
        self.assertNotEqual(getattr(decision.action.card, "name", ""), "祭品")

    def test_invalid_reply_falls_back_to_the_rule(self):
        client = make_mock_client({})
        client.json_chat = lambda system, user: {"choice": "打"}
        tool = CombatSearchTool(tempo=TempoAdvisor(client))
        decision = tool.decide(request(state(hp=60)))
        self.assertEqual(decision.action.card.name, "祭品")

    def test_offline_still_uses_the_rule(self):
        tool = CombatSearchTool(tempo=TempoAdvisor(None))
        decision = tool.decide(request(state(hp=60)))
        self.assertEqual(decision.action.card.name, "祭品")

    def test_same_turn_is_only_asked_once(self):
        calls = []

        def chat(system, user):
            calls.append(user)
            return {"choice": -1, "reason": "留着"}

        client = make_mock_client({})
        client.json_chat = chat
        tool = CombatSearchTool(tempo=TempoAdvisor(client))
        tool.decide(request(state(hp=60)))
        tool.decide(request(state(hp=60)))
        self.assertEqual(len(calls), 1)

    def test_no_tempo_card_means_no_model_call(self):
        client = make_mock_client({})
        client.json_chat = lambda system, user: self.fail("不该询问模型")
        tool = CombatSearchTool(tempo=TempoAdvisor(client))
        decision = tool.decide(request(state(hp=60, hand=[dict(STRIKE, index=0)])))
        self.assertEqual(decision.action.card.name, "打击")


if __name__ == "__main__":
    unittest.main()
