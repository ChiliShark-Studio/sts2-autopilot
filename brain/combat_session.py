"""Fight exactly one combat in the live game -- no routing, no LLM, no rewards.

Why a separate session instead of ``GameAgent.run``: sometimes you only want the
combat algorithm to take over the current fight while you handle the map and
rewards yourself.  That is a different job from "play the whole run", and it
should not drag the map policy (or an LLM client) into the process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .agents.game import GameAgent
from .model import Screen
from .ports import GamePort
from .transport import BridgeError

COMBAT_SCREENS = {Screen.COMBAT, Screen.HAND_SELECT, Screen.CARD_SELECT}


class NotInCombat(RuntimeError):
    pass


@dataclass
class CombatSummary:
    steps: int = 0
    decisions: int = 0
    waits: int = 0
    outcome: str = ""
    hp_before: int = 0
    hp_after: int = 0
    actions: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "decisions": self.decisions,
            "waits": self.waits,
            "outcome": self.outcome,
            "hp_before": self.hp_before,
            "hp_after": self.hp_after,
            "actions": self.actions,
        }


def fight_current(port: GamePort, agent: GameAgent, *, max_steps: int = 300,
                  settle: float = 0.5, verbose: bool = True) -> CombatSummary:
    """Play until the combat is over, then return. Leaves the game on the reward screen."""
    state = port.state()
    if state.screen not in COMBAT_SCREENS:
        raise NotInCombat(f"当前不在战斗中（state_type={state.screen.value}）")

    summary = CombatSummary(hp_before=state.player.hp)
    idle = 0

    for _ in range(max_steps):
        state = port.state()
        summary.steps += 1

        if state.screen not in COMBAT_SCREENS:
            summary.outcome = state.screen.value
            summary.hp_after = state.player.hp
            if verbose:
                print(f"[战斗结束] 进入 {state.screen.value}，血量 {summary.hp_before} -> {summary.hp_after}")
            return summary

        if state.battle is not None and not state.battle.can_act:
            summary.waits += 1
            idle += 1
            if idle > 120:
                summary.outcome = "stuck"
                summary.hp_after = state.player.hp
                return summary
            time.sleep(0.3)
            continue

        idle = 0
        decision = agent.decide(state)
        if decision.action is None:
            summary.waits += 1
            time.sleep(0.3)
            continue

        try:
            port.act(decision.action, state)
        except BridgeError as exc:
            # Screen closed between read and send: re-read instead of aborting.
            if any(token in str(exc) for token in ("not open", "not available", "No card")):
                time.sleep(0.3)
                continue
            raise

        summary.decisions += 1
        summary.actions[decision.action.kind] = summary.actions.get(decision.action.kind, 0) + 1
        if verbose:
            print(f"  {decision.action.describe():26} | {decision.source.value:9} | {decision.reason}")
        time.sleep(settle)

    summary.outcome = "max_steps"
    summary.hp_after = port.state().player.hp
    return summary
