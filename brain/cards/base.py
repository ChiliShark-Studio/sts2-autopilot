"""卡牌父类：统一类型、效果查询与执行接口，实例不保存战斗中的状态。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from .effects import CardEffect, EffectKind, Event, capability

if TYPE_CHECKING:
    from ..combat.effects import EffectState


class CardType(str, Enum):
    ATTACK = "attack"
    SKILL = "skill"
    POWER = "power"


@dataclass(frozen=True)
class BaseCard(ABC):
    upgraded: bool = False
    id: ClassVar[str]
    base_name: ClassVar[str]
    type: ClassVar[CardType]
    cost: ClassVar[int]
    cost_x: ClassVar[bool] = False
    hits_x: ClassVar[bool] = False
    # False 表示基础版，True 表示升级版；仅登记已经录入的版本，不推测缺失数据。
    known_versions: ClassVar[tuple[bool, ...]] = (False, True)

    @property
    def name(self) -> str:
        return self.base_name + ("+" if self.upgraded else "")

    @property
    @abstractmethod
    def effects(self) -> tuple[CardEffect, ...]:
        """子类声明自身效果，搜索可直接查询，执行时保持相同语义。"""

    def play(self, state: EffectState, *, multiplier: int = 1) -> None:
        """执行通过合法性检查后的效果；动作层负责费用与目标校验。"""
        from ..combat.effects import apply_effects

        apply_effects(state, tuple(replace(effect, amount=effect.amount * multiplier) for effect in self.effects))

    def amount(self, event: Event, kind: EffectKind) -> int:
        return capability(self.effects, event, kind)

    def simulation_fields(self) -> dict:
        """将定义适配到尚未全部迁移的模拟卡牌模型，不在此执行结算。"""
        return {"cost": self.cost,
                "per_turn_strength": self.amount(Event.TURN_START, EffectKind.GAIN_STRENGTH),
                "block_per_attack": self.amount(Event.ATTACK_RESOLVED, EffectKind.GAIN_BLOCK)}
