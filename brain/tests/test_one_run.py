"""--one-run 行为测试: 一局结束时停止, 避免循环不断继续."""

from __future__ import annotations

import unittest

from brain.agents.game import GameAgent, StopRun
from brain.contracts import Decision
from brain.model import GameState


class _StubAgent:
    """接受任意请求, 不执行动作."""

    def decide(self, request):  # noqa: ANN001
        return Decision(action=None, reason="stub")


class _Port:
    def __init__(self):
        self.acted = 0

    def state(self):
        return GameState.parse({})

    def act(self, action, state=None):  # noqa: ANN001
        self.acted += 1
        return {"status": "ok"}


def menu():
    return GameState.parse({"state_type": "menu", "menu_screen": "main",
                            "options": ["singleplayer", "settings"]})


def in_run():
    return GameState.parse({"state_type": "map", "map": {"next_options": [{"index": 0, "type": "Monster"}]}})


def agent(**kwargs) -> GameAgent:
    stub = _StubAgent()
    return GameAgent(_Port(), map_agent=stub, build_agent=stub, combat_agent=stub, **kwargs)


class OneRunTests(unittest.TestCase):
    def test_keeps_playing_without_the_flag(self):
        a = agent(stop_after_run=False)
        a.decide(in_run())
        # 回到菜单后应开始下一局, 而不是停止.
        decision = a.decide(menu())
        self.assertIsNotNone(decision.action)

    def test_stops_when_back_at_menu_after_a_run(self):
        a = agent(stop_after_run=True)
        a.decide(in_run())  # 已进入一局游戏
        with self.assertRaises(StopRun):
            a.decide(menu())

    def test_does_not_stop_at_startup_menu(self):
        a = agent(stop_after_run=True)
        decision = a.decide(menu())  # 首次读取时仍在菜单
        self.assertIsNotNone(decision.action)


if __name__ == "__main__":
    unittest.main()
