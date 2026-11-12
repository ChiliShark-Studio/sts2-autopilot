"""地图工具测试: 开局规划路线, 执行计划, 计划失效时重规划, 兜底启发式."""

from __future__ import annotations

import unittest

from brain.contracts import DecisionKind, DecisionRequest, Source
from brain.model import GameState
from brain.tools.map_router import MapRouterTool, fallback, validate_route
from brain.tools.strategy import StrategyContext

# 起点 (1,1) -> (0,2) 普通战斗 或 (2,2) 商店;
# (2,2) 是分叉点, 可去 (2,3) 休息点或 (0,3) 精英, 两条都通向 (1,4) Boss.
ROUTE_MAP = {
    "state_type": "map",
    "map": {
        "current_position": {"col": 1, "row": 1, "type": "Monster"},
        "next_options": [
            {"index": 0, "col": 0, "row": 2, "type": "Monster",
             "leads_to": [{"col": 0, "row": 3, "type": "Elite"}]},
            {"index": 1, "col": 2, "row": 2, "type": "Shop",
             "leads_to": [{"col": 2, "row": 3, "type": "RestSite"}]},
        ],
        "nodes": [
            {"col": 1, "row": 1, "type": "Monster", "children": [[0, 2], [2, 2]]},
            {"col": 0, "row": 2, "type": "Monster", "children": [[0, 3]]},
            {"col": 2, "row": 2, "type": "Shop", "children": [[0, 3], [2, 3]]},
            {"col": 0, "row": 3, "type": "Elite", "children": [[1, 4]]},
            {"col": 2, "row": 3, "type": "RestSite", "children": [[1, 4]]},
            {"col": 1, "row": 4, "type": "Boss", "children": []},
        ],
        "boss": {"name": "墨影幻灵"},
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80, "gold": 120,
               "deck": [{"name": "打击"}, {"name": "防御"}]},
}

# 只有一个候选节点, 用于验证不调用模型.
SINGLE = {
    "state_type": "map",
    "map": {
        "current_position": {"col": 1, "row": 1, "type": "Monster"},
        "next_options": [{"index": 0, "col": 1, "row": 2, "type": "Monster"}],
        "nodes": [
            {"col": 1, "row": 1, "type": "Monster", "children": [[1, 2]]},
            {"col": 1, "row": 2, "type": "Monster", "children": [[1, 3]]},
        ],
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 60, "max_hp": 80, "gold": 120},
}

# 兜底启发式使用的形状: 只需要索引和类型.
FALLBACK_MAP = {
    "state_type": "map",
    "map": {
        "next_options": [
            {"index": 0, "type": "Monster"},
            {"index": 1, "type": "Elite"},
            {"index": 2, "type": "RestSite"},
        ],
        "boss": {"name": "墨影幻灵"},
    },
    "run": {"act": 1, "floor": 4, "ascension": 0},
    "player": {"character": "铁甲战士", "hp": 30, "max_hp": 80, "gold": 40},
}


# 其他测试模块沿用的纯类型地图; 不含坐标, 规划会因缺少节点而回退启发式.
MAP = FALLBACK_MAP


class _Client:
    """记录调用次数并返回固定回复的假模型."""

    def __init__(self, reply=None):
        self.reply = reply
        self.calls = 0
        self.last_meta = {"model": "fake"}

    def json_chat(self, system, user):  # noqa: ANN001
        self.calls += 1
        return self.reply


def request(payload=ROUTE_MAP) -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.MAP, state=GameState.parse(payload))


def tool(client=None, context=None) -> MapRouterTool:
    return MapRouterTool(client, context if context is not None else StrategyContext())


class SingleOptionTests(unittest.TestCase):
    def test_single_option_does_not_call_model(self):
        client = _Client({"route": [[1, 2]], "reason": "随便"})
        decision = tool(client).decide(request(SINGLE))
        self.assertEqual(client.calls, 0)
        self.assertEqual(decision.action.option, 0)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.SCRIPT)
        self.assertIn("唯一可选节点", decision.reason)


class PlanExecutionTests(unittest.TestCase):
    def test_follows_planned_hop_without_model(self):
        context = StrategyContext(route=((2, 2), (2, 3)), route_note="先去商店")
        client = _Client({"route": [[0, 2]], "reason": "不应调用"})
        decision = tool(client, context).decide(request())
        self.assertEqual(client.calls, 0)
        self.assertEqual(decision.action.option, 1)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.SCRIPT)
        self.assertEqual(context.route, ((2, 3),))
        self.assertIn("路线计划", decision.reason)

    def test_replans_when_plan_is_unreachable(self):
        context = StrategyContext(route=((0, 3),), route_note="旧计划")
        client = _Client({"route": [[2, 2], [2, 3]], "reason": "改走商店"})
        decision = tool(client, context).decide(request())
        self.assertEqual(client.calls, 1)
        self.assertEqual(decision.action.option, 1)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.LLM)
        # 第一跳已执行, 剩下的计划留到下一次决策.
        self.assertEqual(context.route, ((2, 3),))

    def test_fork_planning_calls_model_once(self):
        context = StrategyContext()
        client = _Client({"route": [[2, 2], [2, 3]], "reason": "商店后休息"})
        first = tool(client, context).decide(request())
        self.assertEqual(client.calls, 1)
        self.assertEqual(first.action.option, 1)  # type: ignore[union-attr]
        self.assertEqual(context.route, ((2, 3),))

        # 到达 (2,2) 后的下一帧: 计划里还有 (2,3), 不应再调用模型.
        at_fork = {
            **ROUTE_MAP,
            "map": {
                **ROUTE_MAP["map"],
                "current_position": {"col": 2, "row": 2, "type": "Shop"},
                "next_options": [
                    {"index": 0, "col": 0, "row": 3, "type": "Elite"},
                    {"index": 1, "col": 2, "row": 3, "type": "RestSite"},
                ],
            },
        }
        second = tool(client, context).decide(request(at_fork))
        self.assertEqual(client.calls, 1)
        self.assertEqual(second.action.option, 1)  # type: ignore[union-attr]
        self.assertIs(second.source, Source.SCRIPT)

    def test_invalid_route_stops_instead_of_guessing(self):
        context = StrategyContext()
        client = _Client({"route": [[9, 9]], "reason": "不存在"})
        decision = tool(client, context).decide(request())
        # 有模型时不退回启发式: 连续失败就停下.
        self.assertIsNone(decision.action)
        self.assertEqual(client.calls, 3)
        self.assertIn("未给出合法路线", decision.reason)

    def test_no_client_is_heuristic(self):
        decision = tool(None).decide(request())
        self.assertIs(decision.source, Source.HEURISTIC)


class RouteValidationTests(unittest.TestCase):
    def test_truncates_route_at_first_broken_hop(self):
        raw = ROUTE_MAP["map"]
        options = raw["next_options"]
        # (0,2) 与 (2,3) 之间没有连边: 保留前面的合法部分.
        self.assertEqual(validate_route([[0, 2], [2, 3]], raw, options), [(0, 2)])

    def test_accepts_connected_path(self):
        raw = ROUTE_MAP["map"]
        options = raw["next_options"]
        route = validate_route([[2, 2], [2, 3], [1, 4]], raw, options)
        self.assertEqual(route, [(2, 2), (2, 3), (1, 4)])

    def test_rejects_first_hop_outside_options(self):
        raw = ROUTE_MAP["map"]
        self.assertIsNone(validate_route([[0, 3]], raw, raw["next_options"]))

    def test_keeps_prefix_when_later_node_is_unknown(self):
        raw = ROUTE_MAP["map"]
        options = raw["next_options"]
        self.assertEqual(validate_route([[2, 2], [9, 9], [2, 3]], raw, options), [(2, 2)])


class StaleFrameTests(unittest.TestCase):
    """桥接在移动生效前会再返回一帧旧状态: 不能重复选择, 也不能清掉计划."""

    def test_frame_without_chosen_node_is_not_a_new_choice(self):
        context = StrategyContext()
        client = _Client({"route": [[2, 2], [2, 3]], "reason": "先去商店"})
        router = tool(client, context)
        first = router.decide(request())
        self.assertEqual(first.action.option, 1)  # type: ignore[union-attr]
        self.assertEqual(context.route, ((2, 3),))

        # 同一位置, 候选里少了刚选的 (2,2): 这是过渡帧, 应等待.
        stale = {
            **ROUTE_MAP,
            "map": {**ROUTE_MAP["map"],
                    "next_options": [{"index": 0, "col": 0, "row": 2, "type": "Monster"}]},
        }
        second = router.decide(request(stale))
        self.assertIsNone(second.action)
        self.assertEqual(client.calls, 1)
        self.assertEqual(context.route, ((2, 3),))

    def test_plan_survives_transition_and_continues(self):
        context = StrategyContext()
        client = _Client({"route": [[2, 2], [2, 3]], "reason": "先去商店"})
        router = tool(client, context)
        router.decide(request())
        at_fork = {
            **ROUTE_MAP,
            "map": {**ROUTE_MAP["map"],
                    "current_position": {"col": 2, "row": 2, "type": "Shop"},
                    "next_options": [
                        {"index": 0, "col": 0, "row": 3, "type": "Elite"},
                        {"index": 1, "col": 2, "row": 3, "type": "RestSite"},
                    ]},
        }
        decision = router.decide(request(at_fork))
        self.assertEqual(decision.action.option, 1)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.SCRIPT)
        self.assertEqual(client.calls, 1)


class EliteRecheckTests(unittest.TestCase):
    """计划里的精英在低血量时要重新征询, 健康时按计划执行."""

    AT_FORK = {
        **ROUTE_MAP,
        "map": {
            **ROUTE_MAP["map"],
            "current_position": {"col": 2, "row": 2, "type": "Shop"},
            "next_options": [
                {"index": 0, "col": 0, "row": 3, "type": "Elite"},
                {"index": 1, "col": 2, "row": 3, "type": "RestSite"},
            ],
        },
    }

    def _state(self, hp: int):
        return {**self.AT_FORK, "player": {**self.AT_FORK["player"], "hp": hp}}

    def test_low_hp_replans_instead_of_entering_elite(self):
        context = StrategyContext(route=((0, 3), (1, 4)), route_note="原计划走精英")
        client = _Client({"route": [[2, 3], [1, 4]], "reason": "血少, 改走休息点"})
        decision = tool(client, context).decide(request(self._state(30)))
        self.assertEqual(client.calls, 1)
        self.assertEqual(decision.action.option, 1)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.LLM)
        self.assertEqual(context.route, ((1, 4),))

    def test_healthy_keeps_planned_elite(self):
        # 模拟结论是"扛得住"时, 按计划走精英, 不再调用模型.
        from brain.sim.risk import EliteAssessment, EliteRisk

        context = StrategyContext(route=((0, 3), (1, 4)), route_note="原计划走精英")
        client = _Client({"route": [[2, 3]], "reason": "不应调用"})
        router = tool(client, context)
        router._elite_risk = lambda state: EliteAssessment(
            [EliteRisk("bygone_effigy", "旧日雕像", 1.0, 10.0, 12)])
        decision = router.decide(request(self._state(70)))
        self.assertEqual(client.calls, 0)
        self.assertEqual(decision.action.option, 0)  # type: ignore[union-attr]
        self.assertIs(decision.source, Source.SCRIPT)


class FallbackTests(unittest.TestCase):
    def test_fallback_prefers_rest_when_hurt(self):
        index, _ = fallback(GameState.parse(FALLBACK_MAP))
        self.assertEqual(index, 2)

    def test_fallback_prefers_elite_when_healthy(self):
        healthy = {**FALLBACK_MAP, "player": {**FALLBACK_MAP["player"], "hp": 75}}
        index, _ = fallback(GameState.parse(healthy))
        self.assertEqual(index, 1)


if __name__ == "__main__":
    unittest.main()
