"""Card-picker tool: discard / exhaust / upgrade a card from a modal screen.

Silent's starter deck already forces this: ``生存者`` grants block and then asks
you to discard a card.  Without a policy for that screen the agent simply stops
mid-combat, which is exactly what happened in the first live run.

The prompt text is the only signal the bridge gives about intent, so the tool
reads it: upgrade wants the *best* card, discard/exhaust wants the *worst*.
"""

from __future__ import annotations

from ..actions import ConfirmSelection, SelectCard
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..model import Card
from .build_flow import card_score, discard_score

UPGRADE_HINTS = ("升级", "强化", "变为")
KEEP_HINTS = ("保留", "复制")


class SelectTool:
    name = "select_tool"

    def _in_combat_picker(self, state) -> bool:
        # ``combat_select_card`` only applies to the in-combat hand picker;
        # grid pickers (events, transform shrines) use the plain ``select_card``.
        from ..model import Screen

        return state.screen is Screen.HAND_SELECT and state.battle is not None

    def _select(self, state, index: int):
        """In combat the picker lives in NPlayerHand and needs its own action."""
        from ..actions import CombatSelectCard

        return CombatSelectCard(index) if self._in_combat_picker(state) else SelectCard(index)

    def _confirm(self, state):
        from ..actions import CombatConfirmSelection

        return CombatConfirmSelection() if self._in_combat_picker(state) else ConfirmSelection()

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        picker = state.hand_select
        if picker is None or not picker.cards:
            return WAIT

        # Grid pickers are toggles: clicking an already-selected card DESELECTS
        # it. Re-picking the same card is how the first version deadlocked, so
        # only ever click cards that are not selected yet.
        unselected = [c for c in picker.cards if not c.selected]

        if picker.preview_showing or picker.can_confirm:
            # Selection accepted; finish through the confirm button.
            return Decision(action=self._confirm(state), reason="确认选择",
                            source=Source.SCRIPT, tool=self.name)

        if not unselected:
            return Decision(action=self._confirm(state), reason="已选满，确认",
                            source=Source.SCRIPT, tool=self.name)

        prompt = picker.prompt
        if any(hint in prompt for hint in UPGRADE_HINTS) or any(hint in prompt for hint in KEEP_HINTS):
            best = max(unselected, key=card_score)
            return Decision(action=self._select(state, best.index), reason=f"保留/升级 {best.name}（分数最高）",
                            source=Source.HEURISTIC, tool=self.name)

        worst = min(unselected, key=discard_score)
        return Decision(action=self._select(state, worst.index),
                        reason=f"丢弃/移除 {worst.name}（可弃性最低分 {discard_score(worst):.1f}）",
                        source=Source.HEURISTIC, tool=self.name)


def score(card: Card) -> float:  # re-exported for tests
    return card_score(card)
