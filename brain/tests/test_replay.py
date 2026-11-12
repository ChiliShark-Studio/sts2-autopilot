"""回放日志测试: 写入, 读回和汇总."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brain.agents import game as game_module
from brain.agents.game import GameAgent
from brain.contracts import Decision, Source
from brain.model import GameState
from brain.replay import Journal, llm_decisions, replay_states, summarize


class ReplayTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal.create(Path(tmp))
            state = GameState.parse({"state_type": "map", "map": {"next_options": [{"index": 0, "type": "Monster"}]}})
            journal.record(state, Decision(action=None, reason="测试", source=Source.HEURISTIC))
            journal.record(state, Decision(action=None, reason="再来", source=Source.LLM))

            self.assertEqual(journal.step, 2)
            entries = list(journal)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["screen"], "map")
            self.assertEqual(entries[1]["decision"]["source"], "llm")

            states = list(replay_states(journal.path))
            self.assertEqual(len(states), 2)
            self.assertEqual(states[0].screen.value, "map")

            stats = summarize(journal.path)
            self.assertEqual(stats["steps"], 2)
            self.assertEqual(stats["sources"]["llm"], 1)

    def test_llm_extra_survives_and_can_be_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal.create(Path(tmp))
            state = GameState.parse({"state_type": "card_reward", "card_reward": {"cards": []}})
            llm = Decision(action=None, reason="选完美打击", source=Source.LLM, tool="build_flow",
                           extra={"llm": {"model": "deepseek-chat", "prompt": "候选…", "usage": {"total_tokens": 42}}})
            journal.record(state, llm)

            entry = next(iter(journal))
            self.assertEqual(entry["decision"]["extra"]["llm"]["model"], "deepseek-chat")

            llm_rows = list(llm_decisions(journal.path))
            self.assertEqual(len(llm_rows), 1)
            self.assertEqual(llm_rows[0]["decision"]["reason"], "选完美打击")


class _MenuPort:
    """停在主菜单的假桥接, 只记录动作次数."""

    def __init__(self):
        self.acted = 0

    def state(self):
        return GameState.parse({"state_type": "menu", "menu_screen": "main",
                               "options": ["singleplayer"]})

    def act(self, action, state=None):  # noqa: ANN001
        self.acted += 1
        return {"status": "ok"}


class _StubAgent:
    def decide(self, request):  # noqa: ANN001
        return Decision(action=None, reason="stub")


class JournalLatencyTests(unittest.TestCase):
    """主循环必须记录决策耗时: 模型延迟是 T07b 的验收项之一."""

    def test_run_records_elapsed_ms(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal.create(Path(tmp))
            stub = _StubAgent()
            agent = GameAgent(_MenuPort(), map_agent=stub, build_agent=stub,
                             combat_agent=stub, journal=journal)
            # 用两个固定读数替换时钟, 让耗时确定为 250ms, 不依赖真实时间.
            readings = iter([10.0, 10.25])
            fake_time = SimpleNamespace(
                sleep=lambda seconds: None,
                perf_counter=lambda: next(readings, 100.0),
                time=lambda: 0.0,
            )
            with patch.object(game_module, "time", fake_time), \
                    patch.object(GameAgent, "_await_change", lambda self, before: True):
                agent.run(max_steps=1, settle=0)

            entries = list(journal)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["elapsed_ms"], 250)


if __name__ == "__main__":
    unittest.main()
