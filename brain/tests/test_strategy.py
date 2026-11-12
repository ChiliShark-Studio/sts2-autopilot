"""选路和构筑流程回归：只使用假模型，不连接游戏或外部 API。"""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import Mock, patch

from brain.actions import ActionError, DiscardPotion, ShopPurchase, SkipRelicSelection
from brain.contracts import DecisionKind, DecisionRequest, Source
from brain.factory import build_agent
from brain.llm import make_mock_client
from brain.model import GameState
from brain.tools.build_flow import BuildFlowTool
from brain.tools.map_router import MapRouterTool
from brain.tools.strategy import StrategyContext, character_prompt
from brain.tests.test_build_flow import REWARD_STATE, request
from brain.tests.test_map_router import MAP


def client_with(*replies):
    client = make_mock_client({})
    client.json_chat = Mock(side_effect=replies)
    return client


SHOP = {
    "state_type": "shop", "run": {"act": 1, "floor": 5},
    "player": {"character": "铁甲战士", "hp": 45, "max_hp": 80, "gold": 200,
               "deck": [{"index": 0, "name": "打击"}, {"index": 1, "name": "防御"}]},
    "shop": {"can_proceed": True, "items": [
        {"index": 0, "category": "card", "price": 70, "is_stocked": True,
         "can_afford": True, "card_name": "候选牌", "card_description": "造成12点伤害。"},
        {"index": 1, "category": "relic", "price": 90, "is_stocked": True,
         "can_afford": True, "relic_id": "TEST", "relic_description": "测试遗物效果"},
        {"index": 2, "category": "potion", "price": 40, "is_stocked": True,
         "can_afford": True, "potion_id": "NEW", "potion_description": "测试药水效果"},
        {"index": 3, "category": "card_removal", "price": 75,
         "is_stocked": True, "can_afford": True},
    ]},
}


class StrategyFlowTests(unittest.TestCase):
    def test_shop_can_buy_each_category_even_when_proceed_is_enabled(self):
        for index in range(4):
            with self.subTest(index=index):
                client = client_with({"choice": index})
                decision = BuildFlowTool(client).decide(request(SHOP, "shop"))
                self.assertIs(decision.source, Source.LLM)
                self.assertEqual(decision.action.to_payload(), {"action": "shop_purchase", "index": index})
                decision.action.check(GameState.parse(SHOP))

    def test_removal_selection_confirmation_and_second_purchase(self):
        client = client_with(
            {"choice": 3, "build_plan": "减少基础牌，提高关键牌密度"},
            {"choice": 1, "reason": "当前防御过多，删除一张"},
            {"choice": 1},
            {"choice": -1},
        )
        with patch("brain.factory.LLMClient.from_env", return_value=client):
            agent = build_agent()
        decision = agent.decide(GameState.parse(SHOP))
        self.assertEqual(decision.action.to_payload(), {"action": "shop_purchase", "index": 3})

        picker = {"state_type": "card_select", "run": SHOP["run"], "player": SHOP["player"],
                  "card_select": {"screen_type": "select", "prompt": "选择一张牌移除。",
                                  "cards": SHOP["player"]["deck"]}}
        decision = agent.decide(GameState.parse(picker))
        self.assertEqual(decision.action.to_payload(), {"action": "select_card", "index": 1})
        decision.action.check(GameState.parse(picker))
        payload = json.loads(client.json_chat.call_args.args[1])
        self.assertIn("减少基础牌", payload["situation"]["previous_build_plan"])

        picker["card_select"]["can_confirm"] = True
        decision = agent.decide(GameState.parse(picker))
        self.assertEqual(decision.action.kind, "confirm_selection")
        self.assertEqual(client.json_chat.call_count, 2)

        updated = copy.deepcopy(SHOP)
        updated["player"]["gold"] = 125
        updated["player"]["deck"] = updated["player"]["deck"][:1]
        updated["shop"]["items"][3]["is_stocked"] = False
        decision = agent.decide(GameState.parse(updated))
        self.assertEqual(decision.action.to_payload(), {"action": "shop_purchase", "index": 1})
        updated["player"]["gold"] = 35
        updated["shop"]["items"][1]["is_stocked"] = False
        decision = agent.decide(GameState.parse(updated))
        self.assertEqual(decision.action.kind, "proceed")
        payload = json.loads(client.json_chat.call_args.args[1])
        self.assertEqual(payload["situation"]["player"]["gold"], 35)
        self.assertEqual([c["choice"] for c in payload["candidates"]], [-1])

    def test_unavailable_shop_choices_fall_back_without_buying(self):
        shop = copy.deepcopy(SHOP)
        for item in shop["shop"]["items"]:
            item["is_stocked"] = False
        for choice in (0, 99, True, 1.5, "1"):
            with self.subTest(choice=choice):
                client = client_with({"choice": choice})
                decision = BuildFlowTool(client).decide(request(shop, "shop"))
                self.assertIsNot(decision.source, Source.LLM)
                self.assertEqual(decision.action.kind, "proceed")

    def test_offline_shop_can_purchase_then_leave(self):
        tool = BuildFlowTool()
        self.assertEqual(tool.decide(request(SHOP, "shop")).action.kind, "shop_purchase")
        self.assertEqual(tool.decide(request(SHOP, "shop")).action.kind, "proceed")

    def test_shop_inventory_loading_does_not_call_model(self):
        client = client_with()
        decision = BuildFlowTool(client).decide(request(
            {**SHOP, "shop": {"error": "库存尚未加载", "items": [], "can_proceed": True}}, "shop"))
        self.assertIsNone(decision.action)
        client.json_chat.assert_not_called()

    def test_full_belt_replacement_rereads_reward_indices(self):
        state = {"state_type": "rewards", "player": {"max_potion_slots": 3,
                 "potions": [{"slot": i, "id": f"OLD{i}", "description": "旧药水"} for i in range(3)]},
                 "rewards": {"can_proceed": True, "items": [
                     {"index": 0, "type": "gold", "gold_amount": 10},
                     {"index": 1, "type": "potion", "potion_id": "NEW", "potion_description": "新药水"},
                 ]}}
        client = client_with({"choice": -4}, {"choice": 0})
        tool = BuildFlowTool(client)
        self.assertEqual(tool.decide(request(state, "rewards")).action.kind, "claim_reward")
        client.json_chat.assert_not_called()
        state["rewards"]["items"] = state["rewards"]["items"][1:]
        state["rewards"]["items"][0]["index"] = 0
        decision = tool.decide(request(state, "rewards"))
        self.assertEqual(decision.action.to_payload(), {"action": "discard_potion", "slot": 2})
        decision.action.check(GameState.parse(state))
        state["player"]["potions"] = state["player"]["potions"][:2]
        decision = tool.decide(request(state, "rewards"))
        self.assertEqual(decision.action.to_payload(), {"action": "claim_reward", "index": 0})
        payload = json.loads(client.json_chat.call_args.args[1])
        self.assertFalse(any(c["action"]["action"] == "discard_potion" for c in payload["candidates"]))

    def test_shop_full_belt_never_directly_buys_potion(self):
        shop = copy.deepcopy(SHOP)
        shop["player"].update(max_potion_slots=1, potions=[{"slot": 0, "id": "OLD"}])
        client = client_with({"choice": 2}, {"choice": -2}, {"choice": -1})
        tool = BuildFlowTool(client)
        self.assertIsNot(tool.decide(request(shop, "shop")).source, Source.LLM)
        decision = tool.decide(request(shop, "shop"))
        self.assertEqual(decision.action.to_payload(), {"action": "discard_potion", "slot": 0})
        shop["player"]["potions"] = []
        shop["player"]["gold"] = 0
        self.assertEqual(tool.decide(request(shop, "shop")).action.kind, "proceed")
        payload = json.loads(client.json_chat.call_args.args[1])
        self.assertEqual([c["choice"] for c in payload["candidates"]], [-1])

    def test_event_rest_bundle_and_relic_scenes_use_llm(self):
        scenes = [
            ("event", {"event": {"description": "事件的完整背景与规则", "options": [{"index": 0, "is_locked": True},
                                            {"index": 1, "title": "离开"}]}}, "choose_event_option"),
            ("rest_site", {"rest_site": {"options": [{"index": 0, "is_enabled": False},
                                                    {"index": 1, "is_enabled": True, "id": "SMITH"}]}}, "choose_rest_option"),
            ("bundle_select", {"bundle_select": {"bundles": [{"index": 1, "cards": []}]}}, "select_bundle"),
            ("treasure", {"treasure": {"relics": [{"index": 1, "id": "TEST"}]}}, "claim_treasure_relic"),
            ("relic_select", {"relic_select": {"relics": [{"index": 1, "id": "TEST"}]}}, "select_relic"),
        ]
        for scene, extra, kind in scenes:
            with self.subTest(scene=scene):
                client = client_with({"choice": 1})
                with patch("brain.factory.LLMClient.from_env", return_value=client):
                    agent = build_agent()
                state = GameState.parse({"state_type": scene, **extra})
                decision = agent.decide(state)
                self.assertIs(decision.source, Source.LLM)
                self.assertEqual(decision.action.kind, kind)
                self.assertEqual(decision.action.to_payload()["index"], 1)
                payload = json.loads(client.json_chat.call_args.args[1])
                self.assertEqual(payload["situation"]["scene"], extra[scene])

    def test_reward_skip_is_not_reopened_and_flag_resets_on_new_floor(self):
        client = client_with({"choice": -1}, {"choice": -1}, {"choice": 0})
        tool = BuildFlowTool(client)
        tool.decide(request(REWARD_STATE, "card_reward"))
        rewards = {"state_type": "rewards", "rewards": {"can_proceed": True,
                   "items": [{"index": 0, "type": "card"}]}}
        self.assertEqual(tool.decide(request(rewards, "rewards")).action.kind, "proceed")
        payload = json.loads(client.json_chat.call_args.args[1])
        self.assertEqual([c["choice"] for c in payload["candidates"]], [-1])
        rewards["run"] = {"floor": 1}
        self.assertEqual(tool.decide(request(rewards, "rewards")).action.kind, "claim_reward")

    def test_relic_skip_only_when_bridge_allows_it(self):
        raw = {"state_type": "relic_select", "relic_select": {"relics": [{"index": 0}]}}
        with self.assertRaises(ActionError):
            SkipRelicSelection().check(GameState.parse(raw))
        client = client_with({"choice": -1}, {"choice": -1})
        tool = BuildFlowTool(client)
        self.assertEqual(tool.decide(request(raw, "relic_select")).action.kind, "select_relic")
        raw["relic_select"]["can_skip"] = True
        self.assertEqual(tool.decide(request(raw, "relic_select")).action.kind, "skip_relic_selection")


class StrategyContextTests(unittest.TestCase):
    def test_full_effects_and_shared_plan_reach_route_and_reset_at_menu(self):
        state = copy.deepcopy(REWARD_STATE)
        state["player"].update(
            character="铁甲战士",
            deck=[{"id": f"C{i}", "name": f"牌{i}", "description": "完整效果", "is_upgraded": True}
                  for i in range(45)],
            relics=[{"id": "R", "description": "遗物效果", "counter": 2}],
            potions=[{"id": "P", "slot": 2, "description": "药水效果"}],
        )
        client = client_with({"choice": 0, "build_plan": "需要删牌，考虑商店"}, {"choice": 1})
        with patch("brain.factory.LLMClient.from_env", return_value=client):
            agent = build_agent()
        agent.decide(GameState.parse(state))
        first = json.loads(client.json_chat.call_args.args[1])["situation"]
        self.assertEqual(len(first["player"]["deck"]), 45)
        self.assertTrue(first["player"]["deck"][-1]["is_upgraded"])
        self.assertEqual(first["player"]["relics"][0]["counter"], 2)
        self.assertEqual(first["player"]["potions"][0]["description"], "药水效果")
        route = copy.deepcopy(MAP)
        route["player"] = state["player"]
        route["map"]["nodes"] = [{"row": 1, "col": 0, "children": [{"row": 2, "col": 1}]}]
        decision = agent.decide(GameState.parse(route))
        second = json.loads(client.json_chat.call_args.args[1])["situation"]
        self.assertIn("需要删牌", second["previous_build_plan"])
        self.assertEqual(second["map"]["nodes"], route["map"]["nodes"])
        self.assertFalse(second["simulation_available"])
        self.assertEqual(decision.action.kind, "choose_map_node")
        agent.decide(GameState.parse({"state_type": "menu", "options": []}))
        self.assertEqual(agent.strategy_context.plan, "")

    def test_other_character_does_not_receive_ironclad_rules(self):
        prompt = character_prompt(GameState.parse({"player": {"character": "静默猎手"}}))
        self.assertIn("尚无该角色专用攻略", prompt)

    def test_battle_and_combat_pickers_never_call_llm(self):
        client = client_with()
        with patch("brain.factory.LLMClient.from_env", return_value=client):
            agent = build_agent()
        battle = {"state_type": "monster", "battle": {"turn": "player", "ready_for_action": True,
                  "enemies": [{"entity_id": "E", "hp": 10}]},
                  "player": {"hp": 40, "max_hp": 70, "energy": 3, "hand": []}}
        agent.decide(GameState.parse(battle))
        for kind in ("hand_select", "card_select"):
            for has_battle in (False, True):
                raw = {"state_type": kind, "player": {"hand": []}, kind: {
                    "prompt": "选择一张牌消耗。", "cards": [{"index": 0, "name": "打击"}]}}
                if has_battle:
                    raw["battle"] = battle["battle"]
                agent.decide(GameState.parse(raw))
        client.json_chat.assert_not_called()

    def test_upgrade_and_selection_preview_only_request_one_llm_choice(self):
        client = client_with({"choice": 1})
        with patch("brain.factory.LLMClient.from_env", return_value=client):
            agent = build_agent()
        raw = {"state_type": "card_select", "card_select": {"screen_type": "upgrade",
               "cards": [{"index": 0, "name": "防御", "is_selected": True},
                         {"index": 1, "name": "打击"}]}}
        self.assertEqual(agent.decide(GameState.parse(raw)).action.to_payload(),
                         {"action": "select_card", "index": 1})
        choices = json.loads(client.json_chat.call_args.args[1])["candidates"]
        self.assertEqual([c["choice"] for c in choices], [1])
        raw["card_select"]["preview_showing"] = True
        self.assertEqual(agent.decide(GameState.parse(raw)).action.kind, "confirm_selection")
        self.assertEqual(client.json_chat.call_count, 1)

    def test_invalid_model_result_does_not_replace_shared_plan(self):
        context = StrategyContext(plan="原有计划")
        for reply in (None, {"choice": 99}, {"choice": True}, {"choice": 1.2}, {"choice": "1"}):
            client = client_with(reply)
            decision = MapRouterTool(client, context).decide(
                DecisionRequest(kind=DecisionKind.MAP, state=GameState.parse(MAP)))
            self.assertIs(decision.source, Source.HEURISTIC)
            self.assertEqual(context.plan, "原有计划")

    def test_action_validation_rejects_stale_purchases_and_empty_slots(self):
        shop = copy.deepcopy(SHOP)
        shop["player"]["gold"] = 0
        with self.assertRaises(ActionError):
            ShopPurchase(1).check(GameState.parse(shop))
        with self.assertRaises(ActionError):
            DiscardPotion(0).check(GameState.parse(shop))


if __name__ == "__main__":
    unittest.main()
