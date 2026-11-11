"""The game agent: state in, routed decision out. Intentionally small.

Everything domain-specific lives in a tool; everything screen-specific lives in
the router.  This file only owns the lifecycle (menu, game over, waiting) and
the act-after-decide step -- which is exactly the part that must be rewritten
in C# when the brain moves into the mod.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..actions import ChooseMapNode, MenuSelect
from ..contracts import Decision, DecisionKind, Source, WAIT
from ..model import GameState, Screen
from ..ports import GamePort
from ..router import route
from ..transport import BridgeError


class StopRun(Exception):
    """The run is over."""


class UnsupportedScreen(Exception):
    """A screen no tool claims. Stop instead of guessing."""


class GameAgent:
    def __init__(self, port: GamePort, *, map_agent, build_agent, combat_agent,
                 select_agent=None, journal=None, stop_after_run: bool = False) -> None:
        self.port = port
        self.journal = journal
        self.stop_after_run = stop_after_run
        self._character_picked = False
        self._left_menu = False
        self._agents = {
            DecisionKind.MAP: map_agent,
            DecisionKind.BUILD: build_agent,
            DecisionKind.COMBAT: combat_agent,
        }
        if select_agent is not None:
            self._agents[DecisionKind.SELECT] = select_agent

    # -- decision ---------------------------------------------------------- #
    def decide(self, state: GameState) -> Decision:
        if state.screen is Screen.GAME_OVER:
            if self.stop_after_run:
                # One run per process: stop here so the journal can be analysed
                # before the next attempt overwrites the context.
                raise StopRun("run finished")
            # A finished run is lifecycle, not a decision: walk back to the main
            # menu when the game offers it, so a long-running agent can restart.
            options = [str(o) for o in ((state.raw.get("game_over") or {}).get("options") or [])]
            if "main_menu" in options:
                return Decision(action=MenuSelect("main_menu"), reason="离开结算界面",
                                source=Source.SCRIPT, tool="menu")
            raise StopRun("game over")
        if state.screen is Screen.MENU:
            # The game often goes straight back to the main menu after a death,
            # so "we were in a run and are at the menu again" is the reliable
            # end-of-run signal when --one-run is set.
            if self.stop_after_run and self._left_menu:
                raise StopRun("run ended (back at menu)")
            return self._menu(state)

        self._left_menu = True

        request = route(state)
        if request is None:
            if state.screen is Screen.UNKNOWN:
                # The bridge reports "unknown" for a frame or two during screen
                # transitions. Waiting is correct; guessing is not.
                return WAIT
            raise UnsupportedScreen(state.screen.value)
        return self._agents[request.kind].decide(request)

    def _menu(self, state: GameState) -> Decision:
        raw = state.raw
        screen = str(raw.get("menu_screen") or "main")
        # The bridge emits options either as plain strings (main menu) or as
        # {name, enabled} objects (mode / character screens). Normalise both.
        options: list[str] = []
        for item in raw.get("options") or []:
            if isinstance(item, str):
                options.append(item)
            elif isinstance(item, dict) and item.get("enabled", True):
                options.append(str(item.get("name")))

        # Epoch unlocks open the Timeline; leaving it un-revealed hides
        # "singleplayer" from the main menu, so advance through it first.
        if screen == "timeline":
            for preferred in ("advance", "back"):
                if preferred in options:
                    return Decision(action=MenuSelect(preferred), reason=f"Timeline: {preferred}",
                                    source=Source.SCRIPT, tool="menu")
            return WAIT

        if screen == "character_select":
            if not self._character_picked and "IRONCLAD" in options:
                self._character_picked = True
                return Decision(action=MenuSelect("IRONCLAD"), reason="选择角色", source=Source.SCRIPT, tool="menu")
            # 'embark' uses the previous selection; 'confirm' commits the one we
            # just clicked (the bridge says "Use 'confirm' to embark").
            if "confirm" in options:
                self._character_picked = False
                return Decision(action=MenuSelect("confirm"), reason="确认角色并出发",
                                source=Source.SCRIPT, tool="menu")
            return WAIT

        if screen == "tutorial_prompt":
            choice = "no" if "no" in options else (options[0] if options else None)
            if choice:
                return Decision(action=MenuSelect(choice), reason="关闭教程", source=Source.SCRIPT, tool="menu")
            return WAIT

        # Mode select, then the main menu. Order matters: "standard" only
        # exists on the singleplayer mode screen.
        for preferred in ("standard", "continue", "singleplayer", "main_menu"):
            if preferred in options:
                return Decision(action=MenuSelect(preferred), reason=f"菜单: {preferred}",
                                source=Source.SCRIPT, tool="menu")
        return WAIT

    # -- settle guard ------------------------------------------------------ #
    @staticmethod
    def _fingerprint(state: GameState):
        """Cheap signature of 'which screen, at which point in the run'."""
        return (
            state.screen.value,
            state.run.floor,
            state.raw.get("menu_screen"),
            tuple((o.index, o.type) for o in (state.map.options if state.map else ())),
            len(state.rewards),
            len(state.card_reward),
        )

    def _await_change(self, before: GameState, timeout: float = 3.0) -> bool:
        """Wait until the world visibly moved on after a discrete action.

        Choosing a map node or a menu option closes the screen, but the bridge
        can serve one more stale frame of the old screen. Acting again on that
        frame is how you send a duplicate choice the game rejects.
        """
        start = self._fingerprint(before)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.15)
            try:
                now = self.port.state()
            except Exception:
                return False
            if self._fingerprint(now) != start:
                return True
        return False

    # -- loop -------------------------------------------------------------- #
    def step(self) -> Decision:
        state = self.port.state()
        started = time.perf_counter()
        decision = self.decide(state)
        elapsed = int((time.perf_counter() - started) * 1000)

        if decision.action is not None:
            self.port.act(decision.action, state)
        if self.journal is not None:
            self.journal.record(state, decision, elapsed_ms=elapsed)
        return decision

    def run(self, *, max_steps: int = 400, settle: float = 0.6,
            on_step: Optional[Callable[[int, Decision], None]] = None) -> str:
        # ``max_steps`` counts *decisions*, not loop iterations: waiting out an
        # enemy turn can take dozens of frames and must not eat the budget.
        idle = 0
        actions = 0
        iterations = 0
        last_screen = "?"

        while actions < max_steps and iterations < max_steps * 60:
            iterations += 1
            try:
                state = self.port.state()
                last_screen = state.screen.value
                decision = self.decide(state)
                if decision.action is not None:
                    try:
                        self.port.act(decision.action, state)
                    except BridgeError as exc:
                        # The game can close a screen between our read and our
                        # send. That is a lost race, not a bad decision: re-read
                        # on the next tick instead of aborting the run.
                        retryable = ("not open", "not available", "No card", "Not in combat",
                                     "no combat", "not in progress")
                        if any(token in str(exc) for token in retryable):
                            time.sleep(0.4)
                            continue
                        raise
                    if isinstance(decision.action, (ChooseMapNode, MenuSelect)):
                        self._await_change(state)
                if self.journal is not None:
                    self.journal.record(state, decision)
            except StopRun:
                return "game_over"
            except UnsupportedScreen as exc:
                return f"unsupported:{exc}"

            if decision.action is None:
                if decision.tool != "none":
                    # A tool looked at the screen and declined to act. That is a
                    # gap, not a wait -- stop so a human can look once.
                    return f"no_decision:{decision.reason}"
                idle += 1
                # Combat waits (enemy turn, phase transitions, wave spawns) can
                # legitimately last tens of seconds; other screens should not.
                combat_like = last_screen in ("monster", "elite", "boss", "hand_select", "card_select")
                if idle > (150 if combat_like else 40):
                    return f"stuck:{last_screen}"
            else:
                idle = 0
                actions += 1
                if on_step is not None:
                    on_step(actions, decision)

            time.sleep(settle if decision.action is not None else min(settle, 0.3))
        return "max_steps"
