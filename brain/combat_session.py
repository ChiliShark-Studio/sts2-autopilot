"""只接管真实游戏中的一场战斗, 不涉及路线选择, LLM 或奖励.

单独提供会话而不使用 ``GameAgent.run`` 的原因是:
有时只需要算法处理当前战斗, 地图和奖励由用户自己操作.
这与整局自动游玩不同, 无需把地图策略或 LLM 客户端引入流程.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .agents.game import GameAgent
from .model import Screen
from .ports import GamePort
from .transport import BridgeError

COMBAT_SCREENS = {Screen.COMBAT, Screen.ELITE, Screen.BOSS, Screen.HAND_SELECT, Screen.CARD_SELECT}


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
    """持续操作直到战斗结束后返回, 将游戏留在奖励界面."""
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
            # 读取到发送之间界面已关闭, 重新读取而不中断.
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
