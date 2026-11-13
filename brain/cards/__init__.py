"""统一卡牌集入口：每种牌由独立子类提供已知数值、类型和可用版本。

狂怒与恶魔形态已声明共享触发效果，其余旧模板保留现有模拟数值接口；本模块不装配战斗实例。
"""

from .base import BaseCard, CardType
from .catalog import definitions, find_card, get_card
from .demon_form import DemonForm
from .rage import Rage
from .effects import CardEffect, Effect, EffectKind, Event, TriggeredEffect, capability, card_effects

__all__ = ["BaseCard", "CardType", "Rage", "DemonForm", "Effect", "EffectKind", "Event", "definitions", "find_card", "get_card",
           "CardEffect", "TriggeredEffect", "capability", "card_effects"]
