"""Build tool tests: the reward-flow rules that caused the first hang."""

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
        tool.decide(request(REWARD_STATE, "card_reward"))  # opens/marks the card flow
        only_card = {"state_type": "rewards", "player": {"hp": 50, "max_hp": 80},
                     "rewards": {"can_proceed": True, "items": [{"index": 0, "type": "card"}]}}
        decision = tool.decide(request(only_card, "rewards"))
        self.assertIsInstance(decision.action, Proceed)

    def test_unsupported_scene_returns_no_action(self):
        decision = BuildFlowTool().decide(
            request({"state_type": "crystal_sphere", "player": {"hp": 50, "max_hp": 80}}, "crystal_sphere"))
        self.assertIsNone(decision.action)

    def test_effect_only_card_is_not_scored_zero(self):
        # A Rare engine with no damage/block numbers must still beat a blank.
        from brain.model import Card

        engine = Card(name="腐蚀波", cost=1, rarity="Rare", type="Skill")
        blank = Card(name="空白", cost=1, rarity="Basic", type="Skill")
        self.assertGreater(card_score(engine), card_score(blank))

    def test_discard_score_protects_rares(self):
        from brain.model import Card
        from brain.tools.build_flow import discard_score

        rare_engine = Card(name="腐蚀波", cost=1, rarity="Rare", type="Skill")
        basic_defend = Card(name="防御", cost=1, block=5, rarity="Basic", type="Skill")
        # Lower discard_score == safer to throw away.
        self.assertLess(discard_score(basic_defend), discard_score(rare_engine))


if __name__ == "__main__":
    unittest.main()
