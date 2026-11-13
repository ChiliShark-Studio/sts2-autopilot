"""选牌入口：处理空状态、已选卡与确认，再按场景转交构筑或战斗选牌。

场景只使用既有 battle 或玩家 hand 标记判断；本文件不评价该选哪张牌，也不发送动作。
"""

from __future__ import annotations

from ..actions import CombatConfirmSelection, ConfirmSelection
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..model import Screen
from ..ports import ToolPort
from ..strategy.choice import combat_selection


class SelectAgent:
    kind = "select"
    name = "select_tool"

    def __init__(self, build_select: ToolPort, combat_select: ToolPort) -> None:
        self.build_select = build_select
        self.combat_select = combat_select

    def _confirm(self, state):
        in_hand_picker = state.screen is Screen.HAND_SELECT and state.battle is not None
        return CombatConfirmSelection() if in_hand_picker else ConfirmSelection()

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        picker = state.hand_select
        if picker is None or not picker.cards:
            return WAIT

        # 网格选牌是切换操作；没有未选卡时直接确认，避免重复点击。
        unselected = [c for c in picker.cards if not c.selected]
        if picker.preview_showing or picker.can_confirm:
            return Decision(action=self._confirm(state), reason="确认选择",
                            source=Source.SCRIPT, tool=self.name)
        if not unselected:
            return Decision(action=self._confirm(state), reason="已选满，确认",
                            source=Source.SCRIPT, tool=self.name)

        if combat_selection(state):
            return self.combat_select.decide(request)
        return self.build_select.decide(request)
