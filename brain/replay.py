"""Replay journal: every decision, in order, with enough context to re-derive it.

The reference project records state, decision, LLM calls and search records
under ``runs/<seed>/``.  We keep the same idea with less machinery: one JSONL
line per step holding the *wire* state, the routed request and the decision.

That is enough to
  * replay a run offline against a changed policy,
  * diff two policies on the same states,
  * debug one bad decision without re-running the game.

The wire state is stored raw (not the typed model) so a C# port can read the
same file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .contracts import Decision, DecisionRequest
from .model import GameState


@dataclass
class Journal:
    path: Path
    step: int = 0

    @classmethod
    def create(cls, directory: Path) -> "Journal":
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory / f"run-{datetime.now():%Y%m%d-%H%M%S}.jsonl")

    def record(self, state: GameState, decision: Decision, request: Optional[DecisionRequest] = None,
               elapsed_ms: int = 0) -> None:
        self.step += 1
        entry = {
            "step": self.step,
            "screen": state.screen.value,
            "state": state.raw,
            "request": request.to_wire() if request else None,
            "decision": decision.to_wire(),
            "elapsed_ms": elapsed_ms,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def replay_states(path: Path) -> Iterator[GameState]:
    """Yield the states of a recorded run, in order -- feed them to a policy."""
    for entry in Journal(path):
        yield GameState.parse(entry["state"])


def summarize(path: Path) -> dict[str, Any]:
    entries = list(Journal(path))
    screens: dict[str, int] = {}
    sources: dict[str, int] = {}
    tools: dict[str, int] = {}
    for entry in entries:
        screens[entry["screen"]] = screens.get(entry["screen"], 0) + 1
        decision = entry.get("decision") or {}
        sources[decision.get("source", "?")] = sources.get(decision.get("source", "?"), 0) + 1
        tools[decision.get("tool", "?")] = tools.get(decision.get("tool", "?"), 0) + 1
    return {"steps": len(entries), "screens": screens, "sources": sources, "tools": tools}
