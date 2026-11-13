"""战斗内选牌：由 SelectAgent 转交弃牌、消耗、升级和放回牌堆等临时选择。

沿用现有升级增益与卡牌评分，不调用模型；空状态和确认由 SelectAgent 处理。
战斗手牌弹窗使用专用动作，网格弹窗保留网格动作，不改变桥接协议。
"""

from __future__ import annotations

from ..actions import CombatSelectCard, SelectCard
from ..build.scoring import card_score, discard_score
from ..contracts import Decision, DecisionRequest, Source
from ..model import Screen
from ..upgrades import choose_upgrade

UPGRADE_HINTS = ("升级", "强化", "变为")
# 这些提示都是"把牌放到某个位置"而不是丢弃，沿用原有保留评分。
KEEP_HINTS = ("保留", "复制", "抽牌堆顶", "放到", "置于")


class CombatSelect:
    name = "select_tool"

    def _select(self, state, index: int):
        # combat_select_card 只适用于战斗手牌弹窗；网格弹窗使用 select_card。
        in_hand_picker = state.screen is Screen.HAND_SELECT and state.battle is not None
        return CombatSelectCard(index) if in_hand_picker else SelectCard(index)

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        picker = state.hand_select
        unselected = [c for c in picker.cards if not c.selected]
        prompt = picker.prompt
        if any(hint in prompt for hint in UPGRADE_HINTS):
            from .search import ENERGY_VALUE, HP_WEIGHT_FULL, HP_WEIGHT_LOW

            ratio = max(0.0, min(1.0, state.player.hp_ratio))
            weight = HP_WEIGHT_FULL + (HP_WEIGHT_LOW - HP_WEIGHT_FULL) * (1 - ratio)
            position = choose_upgrade(unselected, weight, ENERGY_VALUE)
            if position is not None:
                best = unselected[position]
                return Decision(action=self._select(state, best.index), reason=f"升级 {best.name}（已知升级增益最高）",
                                source=Source.HEURISTIC, tool=self.name)

        if any(hint in prompt for hint in UPGRADE_HINTS) or any(hint in prompt for hint in KEEP_HINTS):
            best = max(unselected, key=card_score)
            return Decision(action=self._select(state, best.index), reason=f"保留/升级 {best.name}（分数最高）",
                            source=Source.HEURISTIC, tool=self.name)

        worst = min(unselected, key=discard_score)
        return Decision(action=self._select(state, worst.index),
                        reason=f"丢弃/移除 {worst.name}（可弃性最低分 {discard_score(worst):.1f}）",
                        source=Source.HEURISTIC, tool=self.name)
