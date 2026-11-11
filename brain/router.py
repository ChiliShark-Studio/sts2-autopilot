"""Screen -> decision owner. The only place that knows the full screen list."""

from __future__ import annotations

from typing import Optional

from .contracts import DecisionKind, DecisionRequest
from .model import GameState, Screen

# Screens owned by the build unit: deck construction and everything that is not
# combat, routing or the menu.
BUILD_SCENES = frozenset({"card_reward", "rewards", "shop", "event", "rest", "rest_site", "treasure"})


def route(state: GameState) -> Optional[DecisionRequest]:
    """Return the request for this screen, or None when the loop owns it.

    Menu and game-over are loop concerns: they are not game decisions, they are
    lifecycle.  Unknown screens also return None so the loop can stop instead
    of inventing a policy.
    """
    if state.screen is Screen.COMBAT:
        return DecisionRequest(kind=DecisionKind.COMBAT, state=state)
    if state.screen is Screen.MAP:
        return DecisionRequest(kind=DecisionKind.MAP, state=state)
    if state.screen in (Screen.HAND_SELECT, Screen.CARD_SELECT):
        return DecisionRequest(kind=DecisionKind.SELECT, state=state, scene=state.screen.value)
    if state.screen.value in BUILD_SCENES:
        return DecisionRequest(kind=DecisionKind.BUILD, state=state, scene=state.screen.value)
    return None


def owner(state: GameState) -> str:
    request = route(state)
    return request.kind.value if request else "loop"
