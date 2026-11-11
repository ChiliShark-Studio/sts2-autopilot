"""The decision contract: the one interface every agent and tool shares.

This file is deliberately boring and JSON-shaped, because the end goal is to
move the whole decision layer into the C# mod.  Anything that lives here must
be expressible as a plain dictionary that a C# implementation can produce and
consume without translation:

    DecisionRequest  { kind, scene, state, legal }
    Decision         { action, reason, source }

Keep protocol concerns (HTTP, files, clocks) out of this file.  If a field
cannot survive ``json.dumps`` it does not belong here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .actions import Action
from .model import GameState


class DecisionKind(str, Enum):
    """Who owns the decision. Mirrors ``router.py`` and the C# switch."""

    MAP = "map"
    BUILD = "build"
    COMBAT = "combat"
    # Modal card pickers (discard / exhaust / upgrade) are their own unit: they
    # interrupt combat but are not combat decisions, and the card pool is the
    # hand rather than the reward list.
    SELECT = "select"


class Source(str, Enum):
    """Where a decision came from -- logged, never parsed by the game."""

    LLM = "llm"
    SEARCH = "search"
    HEURISTIC = "heuristic"
    SCRIPT = "script"
    NONE = "none"


@dataclass(frozen=True)
class DecisionRequest:
    kind: DecisionKind
    state: GameState
    scene: str = ""  # sub-screen for BUILD: card_reward / shop / event / rest / rewards
    legal: tuple[Action, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        """The C#-portable shape. ``state.raw`` is already the wire form."""
        return {
            "kind": self.kind.value,
            "scene": self.scene,
            "state": self.state.raw,
            "legal": [a.to_payload() for a in self.legal],
        }


@dataclass(frozen=True)
class Decision:
    action: Optional[Action] = None
    reason: str = ""
    source: Source = Source.NONE
    tool: str = ""

    @property
    def is_wait(self) -> bool:
        return self.action is None and self.source is Source.NONE

    def to_wire(self) -> dict[str, Any]:
        return {
            "action": self.action.to_payload() if self.action else None,
            "reason": self.reason,
            "source": self.source.value,
            "tool": self.tool,
        }


WAIT = Decision(action=None, reason="等待状态稳定", source=Source.NONE, tool="none")


@dataclass(frozen=True)
class DecisionLog:
    """One entry of the replay journal."""

    step: int
    screen: str
    request: Optional[dict[str, Any]]
    decision: dict[str, Any]
    elapsed_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
