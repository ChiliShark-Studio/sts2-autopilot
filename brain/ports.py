"""Ports: the seams that keep the decision layer portable.

``GamePort`` is what the agent needs from the world (read state, send action).
``ToolPort`` is what an agent needs from a domain tool.  Both are protocols, so

* the live bridge, a recorded replay, and a unit-test fake are interchangeable;
* a C# implementation of the same shape drops in without touching agents.

The live implementation lives in ``transport.py``; a replay reader is in
``replay.py``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .actions import Action
from .contracts import Decision, DecisionRequest
from .model import GameState


@runtime_checkable
class GamePort(Protocol):
    def state(self) -> GameState: ...

    def act(self, action: Action, state: GameState | None = None) -> dict[str, Any]: ...


@runtime_checkable
class ToolPort(Protocol):
    name: str

    def decide(self, request: DecisionRequest) -> Decision: ...
