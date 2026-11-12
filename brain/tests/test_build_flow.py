"""构筑工具测试: 覆盖首次卡住时暴露的奖励流程规则."""

from __future__ import annotations

import unittest

from brain.actions import ClaimReward, Proceed, SelectCardReward, SkipCardReward
from brain.contracts import DecisionKind, DecisionRequest
from brain.model import GameState
from brain.tools.build_flow import BuildFlowTool, card_score

REWARD_STATE = {
    "state_type": "card_reward",
    "card_reward": {"cards": [
        {"id": "A", "name": "踩踏", "type": "Attack", "cost": "3", "damage": 12, "hits": 1, "rarity": "Uncommon", "index": 0},
        {"id": "B", "name": "耸肩无视", "type": "Skill", "cost": "1", "block": 8, "draw": 1, "rarity": "Common", "index": 1},
        {"id": "C", "name": "武装", "type": "Skill", "cost": "1", "block": 5, "rarity": "Common", "index": 2},
    ]},
    "player": {"hp": 50, "max_hp": 80},
}

REWARDS_STATE = {
    "state_type": "rewards",
    "rewards": {"can_proceed": True, "items": [
        {"index": 0, "type": "gold", "gold_amount": 19},
        {"index": 1, "type": "card"},
    ]},
    "player": {"hp": 50, "max_hp": 80},
}


def request(payload: dict, scene: str) -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.BUILD, state=GameState.parse(payload), scene=scene)


class BuildFlowTests(unittest.TestCase):
    def test_picks_highest_scoring_card(self):
        tool = BuildFlowTool()
        decision = tool.decide(request(REWARD_STATE, "card_reward"))
        self.assertIsInstance(decision.action, SelectCardReward)
        self.assertEqual(decision.action.index, 1)  # type: ignore[union-attr]

    def test_low_value_cards_are_skipped(self):
        weak = {"state_type": "card_reward", "player": {"hp": 50, "max_hp": 80},
                "card_reward": {"cards": [{"name": "垃圾", "cost": "2", "block": 3, "index": 0}]}}
        decision = BuildFlowTool().decide(request(weak, "card_reward"))
        self.assertIsInstance(decision.action, SkipCardReward)

    def test_claims_non_card_loot_first(self):
        decision = BuildFlowTool().decide(request(REWARDS_STATE, "rewards"))
        self.assertIsInstance(decision.action, ClaimReward)
        self.assertEqual(decision.action.index, 0)  # type: ignore[union-attr]

    def test_does_not_reopen_card_reward_after_pick(self):
        tool = BuildFlowTool()
        tool.decide(request(REWARD_STATE, "card_reward"))  # 打开并标记选牌流程
        only_card = {"state_type": "rewards", "player": {"hp": 50, "max_hp": 80},
                     "rewards": {"can_proceed": True, "items": [{"index": 0, "type": "card"}]}}
        decision = tool.decide(request(only_card, "rewards"))
        self.assertIsInstance(decision.action, Proceed)

    def test_unsupported_scene_returns_no_action(self):
        decision = BuildFlowTool().decide(
            request({"state_type": "crystal_sphere", "player": {"hp": 50, "max_hp": 80}}, "crystal_sphere"))
        self.assertIsNone(decision.action)

    def test_effect_only_card_is_not_scored_zero(self):
        # 没有伤害或格挡数值的 Rare 核心牌, 评分仍应高于空白牌.
        from brain.model import Card

        engine = Card(name="腐蚀波", cost=1, rarity="Rare", type="Skill")
        blank = Card(name="空白", cost=1, rarity="Basic", type="Skill")
        self.assertGreater(card_score(engine), card_score(blank))

    def test_discard_score_protects_rares(self):
        from brain.model import Card
        from brain.tools.build_flow import discard_score

        rare_engine = Card(name="腐蚀波", cost=1, rarity="Rare", type="Skill")
        basic_defend = Card(name="防御", cost=1, block=5, rarity="Basic", type="Skill")
        # discard_score 越低, 越适合弃掉.
        self.assertLess(discard_score(basic_defend), discard_score(rare_engine))


class EventTests(unittest.TestCase):
    """真理石板会逐步扣除最大生命, 策略不得一直选择继续."""

    def _score(self, option, hp=91, max_hp=91):
        from brain.tools.build_flow import _event_score

        return _event_score(option, hp, max_hp)

    def test_heal_beats_max_hp_cost(self):
        heal = {"title": "砸碎", "description": "回复20点生命。"}
        cost = {"title": "解读", "description": "失去3点最大生命。随机升级一张牌。"}
        self.assertGreater(self._score(heal), self._score(cost))

    def test_refuses_cost_above_the_ratio(self):
        option = {"title": "继续解读", "description": "失去24点最大生命。随机升级一张牌。"}
        self.assertIsNone(self._score(option))  # 24 超过 91 的 15%

    def test_refuses_when_hp_would_drop_to_floor(self):
        option = {"title": "抛下一切", "description": "失去45点最大生命。升级你的所有卡牌。"}
        self.assertIsNone(self._score(option, hp=46, max_hp=91))

    def test_cheap_upgrade_is_considered(self):
        option = {"title": "解读", "description": "失去3点最大生命。随机升级一张牌。"}
        self.assertIsNotNone(self._score(option))

    def test_falls_back_to_leaving_when_everything_is_dangerous(self):
        from brain.contracts import DecisionKind, DecisionRequest
        from brain.model import GameState

        state = {
            "state_type": "event",
            "event": {"event_name": "真理石板", "in_dialogue": False, "options": [
                {"index": 0, "title": "抛下一切", "description": "失去45点最大生命。升级你的所有卡牌。"},
                {"index": 1, "title": "放弃", "description": "停止阅读文字，就这样离开。"},
            ]},
            "player": {"hp": 30, "max_hp": 30},
        }
        tool = BuildFlowTool()
        decision = tool.decide(DecisionRequest(kind=DecisionKind.BUILD,
                                               state=GameState.parse(state), scene="event"))
        self.assertEqual(decision.action.option, 1)  # 应选择离开, 避免付出 45 点生命

    def test_no_safe_option_and_no_leave_still_exits(self):
        from brain.contracts import DecisionKind, DecisionRequest
        from brain.model import GameState

        state = {
            "state_type": "event",
            "event": {"event_name": "真理石板", "in_dialogue": False, "options": [
                {"index": 0, "title": "抛下一切", "description": "失去45点最大生命。升级你的所有卡牌。"},
            ]},
            "player": {"hp": 20, "max_hp": 20},
        }
        decision = BuildFlowTool().decide(DecisionRequest(kind=DecisionKind.BUILD,
                                                          state=GameState.parse(state), scene="event"))
        self.assertIn("危险", decision.reason)


class LLMCardPickTests(unittest.TestCase):
    def test_llm_choice_is_used(self):
        from brain.llm import make_mock_client

        tool = BuildFlowTool(make_mock_client({"choice": 2, "reason": "补输出"}))
        decision = tool.decide(request(REWARD_STATE, "card_reward"))
        self.assertIsInstance(decision.action, SelectCardReward)
        self.assertEqual(decision.action.index, 2)
        self.assertEqual(decision.source.value, "llm")

    def test_llm_skip_is_respected(self):
        from brain.llm import make_mock_client

        tool = BuildFlowTool(make_mock_client({"choice": -1, "reason": "都不想要"}))
        decision = tool.decide(request(REWARD_STATE, "card_reward"))
        self.assertIsInstance(decision.action, SkipCardReward)

    def test_invalid_choice_falls_back_to_heuristic(self):
        from brain.llm import make_mock_client

        tool = BuildFlowTool(make_mock_client({"choice": 99, "reason": "瞎选"}))
        decision = tool.decide(request(REWARD_STATE, "card_reward"))
        self.assertEqual(decision.source.value, "heuristic")
        self.assertIsInstance(decision.action, SelectCardReward)


if __name__ == "__main__":
    unittest.main()
