"""选牌工具: 处理弹窗中的弃牌 / 消耗 / 升级.

静默猎手初始卡组的生存者会先获得格挡, 再要求弃牌.
缺少选牌策略时, Agent 会停在战斗中途, 首次真实对局就遇到过此问题.

桥接仅通过提示文本表达选牌意图, 因此读取提示进行判断:
升级选择高价值牌, 弃牌或消耗选择低价值牌.
"""

from __future__ import annotations

from ..actions import CancelSelection, ConfirmSelection, SelectCard
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..model import Card
from .build_flow import card_score, discard_score
from .strategy import StrategyContext, choose, combat_selection

UPGRADE_HINTS = ("升级", "强化", "变为")
KEEP_HINTS = ("保留", "复制")


class SelectTool:
    name = "select_tool"

    def __init__(self, client=None, context: StrategyContext | None = None) -> None:
        self.client = client
        self.context = context if context is not None else StrategyContext()

    def _in_combat_picker(self, state) -> bool:
        # ``combat_select_card`` 只适用于战斗内手牌选择;
        # 事件或变化祭坛等网格选牌使用 ``select_card``.
        from ..model import Screen

        return state.screen is Screen.HAND_SELECT and state.battle is not None

    def _select(self, state, index: int):
        """战斗内选牌由 NPlayerHand 处理, 需要使用独立动作."""
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

        # 网格选牌是切换操作: 再次点击已选中的牌会取消选择.
        # 初版曾因反复点击同一张牌而卡住,
        # 因此只点击尚未选中的卡牌.
        unselected = [c for c in picker.cards if not c.selected]

        if picker.preview_showing or picker.can_confirm:
            # 选择已被接受, 通过确认按钮完成操作.
            return Decision(action=self._confirm(state), reason="确认选择",
                            source=Source.SCRIPT, tool=self.name)

        if not unselected:
            return Decision(action=self._confirm(state), reason="已选满，确认",
                            source=Source.SCRIPT, tool=self.name)

        if not combat_selection(state):
            from dataclasses import asdict

            candidates = [(c.index, self._select(state, c.index), asdict(c)) for c in unselected]
            raw_picker = state.raw.get("card_select") or state.raw.get("hand_select") or {}
            if raw_picker.get("can_skip"):
                candidates.append((-1, CancelSelection(), {"说明": "跳过本次选牌"}))
            decision = choose(
                self.client, state, self.context,
                task=f"构筑选牌：类型={picker.mode}，提示={picker.prompt}。永久删牌、升级、变化或复制需要服务于当前构筑。",
                candidates=candidates, tool=self.name,
            )
            if decision is not None:
                return decision

        prompt = picker.prompt
        if any(hint in prompt for hint in UPGRADE_HINTS) or any(hint in prompt for hint in KEEP_HINTS):
            best = max(unselected, key=card_score)
            return Decision(action=self._select(state, best.index), reason=f"保留/升级 {best.name}（分数最高）",
                            source=Source.HEURISTIC, tool=self.name)

        worst = min(unselected, key=discard_score)
        return Decision(action=self._select(state, worst.index),
                        reason=f"丢弃/移除 {worst.name}（可弃性最低分 {discard_score(worst):.1f}）",
                        source=Source.HEURISTIC, tool=self.name)


def score(card: Card) -> float:  # 重新导出供测试使用
    return card_score(card)
