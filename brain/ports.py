"""接口边界: 保持决策层可移植.

``GamePort`` 提供读取状态和发送动作的能力.
``ToolPort`` 提供 Agent 所需的决策工具接口. 两者均为协议, 因此:

* 实时桥接, 历史回放和测试替身可以通过相同接口接入.
* C# 可以实现相同形状, 无需改变 Agent 的职责.

实时实现位于 ``transport.py``, 回放读取逻辑位于 ``replay.py``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .actions import Action
from .contracts import Decision, DecisionRequest
from .model import GameState


@runtime_checkable
class GamePort(Protocol):
    def state(self) -> GameState: ...

    def act(self, action: Action, state: GameState | None = None) -> dict[str, Any]: ...


@runtime_checkable
class ToolPort(Protocol):
    name: str

    def decide(self, request: DecisionRequest) -> Decision: ...
