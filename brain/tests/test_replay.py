"""Replay journal tests: write, read back, summarize."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.contracts import Decision, Source
from brain.model import GameState
from brain.replay import Journal, replay_states, summarize


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


if __name__ == "__main__":
    unittest.main()
