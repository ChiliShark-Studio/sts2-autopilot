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
from ..upgrades import choose_upgrade
from .build_flow import card_score, discard_score
from .strategy import LLM_ATTEMPTS, StrategyContext, choose, combat_selection, no_decision

UPGRADE_HINTS = ("升级", "强化", "变为")
# 这些提示都是"把牌放到某个位置"而不是丢弃:
# 头槌会把选中的牌放到抽牌堆顶, 此时应该挑最想要的牌, 而不是最差的.
KEEP_HINTS = ("保留", "复制", "抽牌堆顶", "放到", "置于")


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
            if self.client is not None:
                decision = choose(
                    self.client, state, self.context,
                    task=f"构筑选牌：类型={picker.mode}，提示={picker.prompt}。永久删牌、升级、变化或复制需要服务于当前构筑。",
                    candidates=candidates, tool=self.name, attempts=LLM_ATTEMPTS,
                )
                # 有模型时不退回启发式: 模型连续失败就停下等待排查.
                return decision if decision is not None else no_decision(
                    self.name, f"模型连续 {LLM_ATTEMPTS} 次未给出有效选择")

        prompt = picker.prompt
        if combat_selection(state) and any(hint in prompt for hint in UPGRADE_HINTS):
            from ..combat.search import ENERGY_VALUE, HP_WEIGHT_FULL, HP_WEIGHT_LOW

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


def score(card: Card) -> float:  # 重新导出供测试使用
    return card_score(card)
