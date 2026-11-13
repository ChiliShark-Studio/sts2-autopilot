"""构筑选牌：由 SelectAgent 转交非战斗中的永久删牌、升级、变化或复制选择。

有模型时结合本局构筑计划选择，离线时沿用现有卡牌评分；空状态和确认由 SelectAgent 处理。
本模块只返回选择动作，不操作游戏，也不处理战斗内临时选牌。
"""

from __future__ import annotations

from dataclasses import asdict

from ..actions import CancelSelection, SelectCard
from ..contracts import Decision, DecisionRequest, Source
from ..strategy.choice import LLM_ATTEMPTS, choose, no_decision
from ..strategy.context import StrategyContext
from .scoring import card_score, discard_score

UPGRADE_HINTS = ("升级", "强化", "变为")
KEEP_HINTS = ("保留", "复制", "抽牌堆顶", "放到", "置于")


class BuildSelect:
    name = "select_tool"

    def __init__(self, client=None, context: StrategyContext | None = None) -> None:
        self.client = client
        self.context = context if context is not None else StrategyContext()

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        picker = state.hand_select
        unselected = [c for c in picker.cards if not c.selected]
        candidates = [(c.index, SelectCard(c.index), asdict(c)) for c in unselected]
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
        if any(hint in prompt for hint in UPGRADE_HINTS) or any(hint in prompt for hint in KEEP_HINTS):
            best = max(unselected, key=card_score)
            return Decision(action=SelectCard(best.index), reason=f"保留/升级 {best.name}（分数最高）",
                            source=Source.HEURISTIC, tool=self.name)

        worst = min(unselected, key=discard_score)
        return Decision(action=SelectCard(worst.index),
                        reason=f"丢弃/移除 {worst.name}（可弃性最低分 {discard_score(worst):.1f}）",
                        source=Source.HEURISTIC, tool=self.name)
