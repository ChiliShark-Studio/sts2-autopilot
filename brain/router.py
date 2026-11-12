"""界面到决策单元的路由, 集中维护所有界面类型."""

from __future__ import annotations

from typing import Optional

from .contracts import DecisionKind, DecisionRequest
from .model import GameState, Screen

# 构筑单元负责卡组构筑, 以及战斗, 路线和菜单之外的界面.
# 这些界面统一交给构筑工具处理.
BUILD_SCENES = frozenset({"card_reward", "rewards", "shop", "event", "rest", "rest_site",
                          "treasure", "bundle_select", "relic_select"})


def route(state: GameState) -> Optional[DecisionRequest]:
    """根据当前界面生成决策请求; 由主循环处理的界面返回 None.

    菜单和结算界面属于生命周期管理, 由主循环负责, 不交给策略工具决策.
    未知界面也返回 None, 交给主循环处理, 避免猜测操作.
    """
    if state.screen in (Screen.COMBAT, Screen.ELITE, Screen.BOSS):
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
