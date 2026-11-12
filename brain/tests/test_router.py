"""路由与契约测试, 无需游戏或网络."""

from __future__ import annotations

import unittest

from brain.contracts import DecisionKind, DecisionRequest
from brain.model import GameState
from brain.router import owner, route


def state(kind: str, **extra) -> GameState:
    payload = {"state_type": kind}
    payload.update(extra)
    return GameState.parse(payload)


class RouterTests(unittest.TestCase):
    def test_combat_routes_to_combat(self):
        request = route(state("monster", battle={"ready_for_action": True, "turn": "player"}))
        self.assertIsNotNone(request)
        self.assertIs(request.kind, DecisionKind.COMBAT)

    def test_map_routes_to_map(self):
        request = route(state("map", map={"next_options": [{"index": 0, "type": "Monster"}]}))
        self.assertIsNotNone(request)
        self.assertIs(request.kind, DecisionKind.MAP)

    def test_card_reward_routes_to_build_with_scene(self):
        request = route(state("card_reward", card_reward={"cards": []}))
        self.assertIsNotNone(request)
        self.assertIs(request.kind, DecisionKind.BUILD)
        self.assertEqual(request.scene, "card_reward")

    def test_menu_is_loop_owned(self):
        self.assertIsNone(route(state("menu")))
        self.assertEqual(owner(state("menu")), "loop")

    def test_unknown_screen_has_no_owner(self):
        self.assertIsNone(route(state("weird_new_screen")))

    def test_game_over_is_loop_owned(self):
        self.assertIsNone(route(state("game_over")))


class ContractTests(unittest.TestCase):
    def test_request_wire_shape_is_json_safe(self):
        request = DecisionRequest(kind=DecisionKind.MAP, state=state("map"))
        wire = request.to_wire()
        # 必须支持 JSON 序列化和反序列化, 后续 C# 移植读取相同形状.
        import json

        self.assertEqual(json.loads(json.dumps(wire))["kind"], "map")
        self.assertIn("state", wire)


if __name__ == "__main__":
    unittest.main()
