"""游戏 Agent: 输入状态, 路由后输出决策, 保持实现精简.

领域逻辑放在工具中, 界面分发放在路由中.
本文件只负责生命周期 (菜单, 结算, 等待) 和决策后的动作执行.
将决策层搬进 Mod 时, 这部分需要用 C# 重写.
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
    """一局游戏已经结束."""


class UnsupportedScreen(Exception):
    """没有工具负责当前界面, 停止运行以避免猜测操作."""


class GameAgent:
    def __init__(self, port: GamePort, *, map_agent, build_agent, combat_agent,
                 select_agent=None, journal=None, stop_after_run: bool = False,
                 strategy_context=None) -> None:
        self.port = port
        self.journal = journal
        self.stop_after_run = stop_after_run
        self.strategy_context = strategy_context
        self._character_picked = False
        self._left_menu = False
        self._agents = {
            DecisionKind.MAP: map_agent,
            DecisionKind.BUILD: build_agent,
            DecisionKind.COMBAT: combat_agent,
        }
        if select_agent is not None:
            self._agents[DecisionKind.SELECT] = select_agent

    # -- 决策 --
    def decide(self, state: GameState) -> Decision:
        if self.strategy_context is not None and state.screen in (Screen.MENU, Screen.GAME_OVER):
            self.strategy_context.reset()
        if state.screen is Screen.GAME_OVER:
            if self.stop_after_run:
                # 每个进程只运行一局: 在这里停止, 便于先分析日志,
                # 避免下一次尝试覆盖当前上下文.
                raise StopRun("run finished")
            # 对局结束属于生命周期处理; 游戏提供返回入口时,
            # 回到主菜单, 让持续运行的 Agent 可以重新开局.
            options = [str(o) for o in ((state.raw.get("game_over") or {}).get("options") or [])]
            if "main_menu" in options:
                return Decision(action=MenuSelect("main_menu"), reason="离开结算界面",
                                source=Source.SCRIPT, tool="menu")
            raise StopRun("game over")
        if state.screen is Screen.MENU:
            # 死亡后游戏经常直接回到主菜单, 因此设置 --one-run 时,
            # 可以用 "已经进入过对局, 现在又回到菜单"
            # 作为本局结束的可靠信号.
            if self.stop_after_run and self._left_menu:
                raise StopRun("run ended (back at menu)")
            return self._menu(state)

        self._left_menu = True

        request = route(state)
        if request is None:
            if state.screen is Screen.UNKNOWN:
                # 界面切换时, 桥接可能有一两帧返回 "unknown".
                # 此时应等待状态稳定, 避免猜测操作.
                return WAIT
            raise UnsupportedScreen(state.screen.value)
        # 按 request.kind 分发给对应 Agent:
        # COMBAT -> CombatAgent: 普通战斗, 精英和 Boss 的出牌及药水决策.
        # MAP -> MapAgent: 选择下一个地图节点.
        # BUILD -> BuildAgent: 奖励, 奖励选牌, 商店, 事件, 篝火, 宝箱和卡牌包.
        # SELECT -> SelectAgent: 弃牌, 消耗, 升级等选牌弹窗.
        return self._agents[request.kind].decide(request)

    def _menu(self, state: GameState) -> Decision:
        raw = state.raw
        screen = str(raw.get("menu_screen") or "main")
        # 桥接选项既可能是字符串 (主菜单), 也可能是
        # {name, enabled} 对象 (模式 / 角色界面), 在这里统一处理.
        options: list[str] = []
        for item in raw.get("options") or []:
            if isinstance(item, str):
                options.append(item)
            elif isinstance(item, dict) and item.get("enabled", True):
                options.append(str(item.get("name")))

        # Epoch 解锁后会打开 Timeline; 未完成揭示时,
        # 主菜单会隐藏 "singleplayer", 因此先推进该流程.
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
            # 'embark' 使用上次选择; 'confirm' 提交刚选中的角色.
            # 桥接提示使用 confirm 开始对局.
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

        # 先处理模式选择, 再处理主菜单. 顺序很重要:
        # "standard" 只会出现在单人模式选择界面.
        for preferred in ("standard", "continue", "singleplayer", "main_menu"):
            if preferred in options:
                return Decision(action=MenuSelect(preferred), reason=f"菜单: {preferred}",
                                source=Source.SCRIPT, tool="menu")
        return WAIT

    # -- 状态稳定检查 --
    @staticmethod
    def _fingerprint(state: GameState):
        """用低成本签名标识当前界面及本局所处位置."""
        return (
            state.screen.value,
            state.run.floor,
            state.raw.get("menu_screen"),
            tuple((o.index, o.type) for o in (state.map.options if state.map else ())),
            len(state.rewards),
            len(state.card_reward),
        )

    def _await_change(self, before: GameState, timeout: float = 3.0) -> bool:
        """执行一次离散动作后, 等待游戏状态发生可观察的变化.

        选择地图节点或菜单选项会关闭当前界面, 但桥接可能仍返回一帧旧状态.
        若再次对该帧执行动作, 就会发送被游戏拒绝的重复选择.
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

    # -- 主循环 --
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
        # ``max_steps`` 统计决策次数, 不统计循环次数:
        # 等待敌方回合可能经过几十帧, 不应消耗决策预算.
        idle = 0
        actions = 0
        iterations = 0
        last_screen = "?"

        while actions < max_steps and iterations < max_steps * 60:
            iterations += 1
            try:
                state = self.port.state()
                last_screen = state.screen.value
                started = time.perf_counter()
                decision = self.decide(state)
                # 真实模型调用耗时是 T07b 的验收项之一, 因此每步都要记录.
                elapsed = int((time.perf_counter() - started) * 1000)
                if decision.action is not None:
                    try:
                        self.port.act(decision.action, state)
                    except BridgeError as exc:
                        # 读取状态到发送动作之间, 游戏可能已经关闭当前界面.
                        # 这是状态变化引起的时序竞争, 下一轮应重新读取,
                        # 无需因此中断整局游戏.
                        # 领取卡牌奖励后, 奖励界面可能还在播放, 此时继续按钮尚未可用;
                        # 这类"按钮还没出来"的错误同样只需重新读取状态.
                        retryable = ("not open", "not available", "No card", "Not in combat",
                                     "no combat", "not in progress", "No proceed button",
                                     "proceed button", "not enabled")
                        if any(token in str(exc) for token in retryable):
                            time.sleep(0.4)
                            continue
                        raise
                    if isinstance(decision.action, (ChooseMapNode, MenuSelect)):
                        self._await_change(state)
                if self.journal is not None:
                    self.journal.record(state, decision, elapsed_ms=elapsed)
            except StopRun:
                return "game_over"
            except UnsupportedScreen as exc:
                return f"unsupported:{exc}"

            if decision.action is None:
                if decision.tool != "none":
                    # 工具已检查界面却没有给出动作, 表示策略存在缺口,
                    # 应停止供人工检查, 而不是继续等待.
                    return f"no_decision:{decision.reason}"
                idle += 1
                # 战斗等待 (敌方回合, 阶段切换, 新一波敌人出现)
                # 可能持续几十秒; 其他界面不应等待这么久.
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
