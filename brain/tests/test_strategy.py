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
from brain.build.flow import BuildFlowTool
from brain.map.router import MapRouterTool
from brain.strategy.context import StrategyContext
from brain.strategy.prompts import NEOW_GUIDE, character_prompt, event_prompt
from brain.tests.test_build_flow import REWARD_STATE, request
from brain.tests.test_map_router import MAP


def client_with(*replies):
    """按顺序返回回复; 用完最后一个回复后持续重复它, 便于测试重试逻辑."""
    client = make_mock_client({})
    client.json_chat = Mock(side_effect=lambda system, user: replies[min(client.json_chat.call_count - 1,
                                                                         len(replies) - 1)])
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

    def test_unavailable_shop_choices_never_get_bought(self):
        shop = copy.deepcopy(SHOP)
        for item in shop["shop"]["items"]:
            item["is_stocked"] = False
        for choice in (0, 99, True, 1.5, "1"):
            with self.subTest(choice=choice):
                client = client_with({"choice": choice})
                decision = BuildFlowTool(client).decide(request(shop, "shop"))
                # 不可购买的选项不会被执行: 模型连续给出非法选择时停下.
                self.assertIsNone(decision.action)
                self.assertIsNot(decision.source, Source.LLM)

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
        client = client_with({"choice": 2})
        tool = BuildFlowTool(client)
        decision = tool.decide(request(shop, "shop"))
        payload = json.loads(client.json_chat.call_args.args[1])
        # 满槽时在售药水不出现在直接购买候选里.
        direct = [c for c in payload["candidates"]
                  if c["action"].get("action") == "shop_purchase"
                  and (c.get("details") or {}).get("category") == "potion"]
        self.assertEqual(direct, [])
        # 模型给的是"买药水"这个不存在的选项, 因此不会执行任何购买.
        self.assertIsNone(decision.action)

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
        client = client_with({"choice": 0}, {"choice": -1})
        tool = BuildFlowTool(client)
        self.assertEqual(tool.decide(request(raw, "relic_select")).action.kind, "select_relic")
        raw["relic_select"]["can_skip"] = True
        self.assertEqual(tool.decide(request(raw, "relic_select")).action.kind, "skip_relic_selection")


class NeowPromptTests(unittest.TestCase):
    """涅奥开局选项分级只在涅奥事件里出现, 不污染每步提示词."""

    def test_guide_is_injected_for_neow_event_only(self):
        neow = {"state_type": "event",
                "event": {"event_id": "NEOW", "event_name": "涅奥",
                          "options": [{"index": 0, "title": "金色珍珠"}, {"index": 1, "title": "卷轴箱"}]}}
        self.assertEqual(event_prompt(GameState.parse(neow)), NEOW_GUIDE)
        client = client_with({"choice": 1})
        decision = BuildFlowTool(client).decide(request(neow, "event"))
        self.assertIs(decision.source, Source.LLM)
        self.assertIn("涅奥开局选项优先级", client.json_chat.call_args.args[0])

        other = {"state_type": "event",
                 "event": {"event_id": "TRUTH_TABLET", "event_name": "真理石板",
                           "options": [{"index": 0, "title": "升级"}, {"index": 1, "title": "离开"}]}}
        self.assertEqual(event_prompt(GameState.parse(other)), "")
        client = client_with({"choice": 1})
        BuildFlowTool(client).decide(request(other, "event"))
        self.assertNotIn("涅奥开局选项优先级", client.json_chat.call_args.args[0])

    def test_guide_is_not_repeated_in_character_prompt(self):
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertNotIn("涅奥开局选项优先级", prompt)

    def test_guide_ranks_transition_before_ceiling_and_flags_scroll_box(self):
        # 卷轴箱此前被选中并塞入铁斩波: 分级里必须标明它只是"没有更好选择才拿".
        self.assertIn("卷轴箱", NEOW_GUIDE)
        self.assertIn("赌运气", NEOW_GUIDE)
        self.assertIn("过渡", NEOW_GUIDE)
        self.assertIn("80%", NEOW_GUIDE)

    def test_bundle_prompt_warns_cards_are_permanent(self):
        bundle = {"state_type": "bundle_select",
                  "bundle_select": {"bundles": [{"index": 0, "cards": []}, {"index": 1, "cards": []}]}}
        client = client_with({"choice": 1})
        BuildFlowTool(client).decide(request(bundle, "bundle_select"))
        self.assertIn("永久加入牌组", client.json_chat.call_args.args[0])


    def test_iron_wave_is_never_offered(self):
        # 铁斩波这类平庸的攻防一体牌: 候选里直接剔除, 模型没有机会选.
        reward = {"state_type": "card_reward",
                  "card_reward": {"cards": [{"index": 0, "id": "IRON_WAVE", "name": "铁斩波",
                                                "cost": "1", "damage": 5, "block": 5}]},
                  "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80}}
        decision = BuildFlowTool().decide(request(reward, "card_reward"))
        self.assertEqual(decision.action.kind, "skip_card_reward")

        client = client_with({"choice": 0})
        reward_mixed = {"state_type": "card_reward",
                        "card_reward": {"cards": [
                            {"index": 0, "id": "IRON_WAVE", "name": "铁斩波", "cost": "1",
                             "damage": 5, "block": 5},
                            {"index": 1, "id": "STONE_ARMOR", "name": "岩石铠甲", "cost": "1",
                             "description": "获得4层覆甲。"}]},
                        "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80}}
        BuildFlowTool(client).decide(request(reward_mixed, "card_reward"))
        payload = json.loads(client.json_chat.call_args.args[1])
        offered = [item["details"].get("name") for item in payload["candidates"]]
        self.assertNotIn("铁斩波", offered)
        self.assertIn("岩石铠甲", offered)


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
        client = client_with({"choice": 0, "build_plan": "需要删牌，考虑商店"},
                             {"route": [[0, 1]], "reason": "测试路线"})
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
        route["map"]["next_options"] = [
            {"index": 0, "col": 0, "row": 1, "type": "Monster"},
            {"index": 1, "col": 1, "row": 1, "type": "Monster"},
        ]
        route["map"]["nodes"] = [{"row": 1, "col": 0, "children": [{"row": 2, "col": 1}]},
                                 {"row": 1, "col": 1, "children": [{"row": 2, "col": 1}]},
                                 {"row": 2, "col": 1, "type": "Monster", "children": []}]
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

    def test_ironclad_prompt_includes_campfire_rule(self):
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("篝火默认锻造", prompt)
        self.assertIn("不要超过本幕篝火总数的一半", prompt)

    def test_ironclad_prompt_limits_filler_cards(self):
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("前五层可以抓高效过渡牌", prompt)
        self.assertIn("优先攒钱", prompt)

    def test_ironclad_prompt_is_archetype_driven(self):
        # 张数不是目标: 先定主线, 每张牌都要服务主线; 删牌先删打击.
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("先定主线", prompt)
        self.assertIn("每张牌都要回答", prompt)
        self.assertIn("先删打击", prompt)
        self.assertIn("删牌优先于买新牌", prompt)

    def test_prompt_forbids_citing_a_mainline_without_its_engine(self):
        # 第 7 层把飞剑回旋镖当"服务力量主线"选中, 当时牌组里根本没有力量来源.
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("没有引擎就不算", prompt)
        self.assertIn("1 费 9 伤", prompt)

    def test_trap_cards_from_the_tier_list_are_never_offered(self):
        # 攻略里明确垫底的牌 (破击/余烬, 约 31% 胜率) 与铁斩波一起排除.
        from brain.build.scoring import NEVER_PICK

        for name in ("铁斩波", "破击", "余烬"):
            self.assertIn(name, NEVER_PICK)
        reward = {"state_type": "card_reward",
                  "card_reward": {"cards": [
                      {"index": 0, "id": "BREAK", "name": "破击", "cost": "1"},
                      {"index": 1, "id": "CINDER", "name": "余烬", "cost": "1"}]},
                  "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80}}
        decision = BuildFlowTool().decide(request(reward, "card_reward"))
        self.assertEqual(decision.action.kind, "skip_card_reward")

    def test_prompt_mentions_the_ancient_tier_rule(self):
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("先古事件", prompt)
        self.assertIn("烘焙手套", prompt)
        self.assertIn("event_guide.ancient", prompt)

    def test_prompt_explains_energy_icon_text(self):
        # 描述里的 4[energy_icon] 被读成"41能量", 需要说明图标含义.
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("[energy_icon]", prompt)

    def test_prompt_points_at_the_event_data(self):
        # 复盘: 脑蛭选了"分享知识"(lift -0.8), 而"把它扯下来"是 +6.2.
        # 具体数据不再写死在提示词里, 改为注入 situation.event_guide.
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("事件选择", prompt)
        self.assertIn("event_guide", prompt)
        self.assertIn("提升度", prompt)

    def test_prompt_judges_enchant_events_by_effect(self):
        # 自助指南的"能力附魔提升度高"是选择偏差: 迅速只触发一次, 锋利是永久加伤.
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("只触发一次", prompt)
        self.assertIn("附魔类事件按效果算", prompt)
        self.assertIn("锋利", prompt)

    def test_knowledge_table_covers_the_sharp_multihit_combo(self):
        # 锋利 + 飞剑回旋镖是这局实际用到的 combo, 现在放在结构化知识表里按牌组注入.
        from brain.strategy.knowledge import relevant_notes

        notes = relevant_notes(["飞剑回旋镖+", "打击"])
        topics = [item["topic"] for item in notes]
        self.assertIn("附魔组合·锋利", topics)
        text = " ".join(item["note"] for item in notes)
        self.assertIn("每段都吃", text)

    def test_knowledge_only_injects_matching_entries(self):
        from brain.strategy.knowledge import relevant_notes

        self.assertEqual(relevant_notes(["打击"]), [])
        self.assertEqual([item["topic"] for item in relevant_notes(["防御"])],
                         ["附魔组合·灵巧"])
        self.assertEqual([item["topic"] for item in relevant_notes(["残酷"])],
                         ["残酷 + 易伤"])

    def test_prompt_has_deck_size_rule(self):
        # 复盘: 第 3 层、8 张牌组却跳过了卡牌奖励, 理由是"不稀释"——薄牌组谈不上稀释.
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("少于 12 张", prompt)
        self.assertIn("deck_fit.deck_size", prompt)
        self.assertIn("一定要拿", prompt)

    def test_prompt_limits_hybrid_common_cards(self):
        # 第 4 层把铁斩波当"补格挡"选中, 同一奖励里就有第二张岩石铠甲 (可叠层).
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("铁斩波", prompt)
        self.assertIn("0 张", prompt)
        self.assertIn("叠层", prompt)

    def test_prompt_prioritises_block_when_it_is_the_gap(self):
        prompt = character_prompt(GameState.parse({"player": {"character": "铁甲战士"}}))
        self.assertIn("block_cards", prompt)
        self.assertIn("先补短板再补强度", prompt)
        self.assertIn("武装", prompt)

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
            # 模型给出无效结果时停下, 既不猜动作, 也不覆盖已有计划.
            self.assertIsNone(decision.action)
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
