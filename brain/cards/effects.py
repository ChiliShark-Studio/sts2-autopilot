"""卡牌效果声明、能力查询及旧模型适配；战斗结算位于 brain.combat.effects。"""

from dataclasses import dataclass, replace
from enum import Enum


class Event(str, Enum):
    TURN_START = "turn_start"
    ATTACK_RESOLVED = "attack_resolved"
    TURN_END = "turn_end"


class EffectKind(str, Enum):
    GAIN_BLOCK = "gain_block"
    GAIN_STRENGTH = "gain_strength"


@dataclass(frozen=True)
class Effect:
    kind: EffectKind
    amount: int
    power_id: str = ""


@dataclass(frozen=True)
class TriggeredEffect:
    """卡牌声明的完整持续效果；规则对象本身不保存战斗状态。"""

    power_id: str
    name: str
    event: Event
    action: EffectKind
    amount: int
    expires: Event | None = None


CardEffect = Effect | TriggeredEffect


def card_effects(*, per_turn_strength: int = 0, block_per_attack: int = 0) -> tuple[CardEffect, ...]:
    """迁移入口：将既有卡牌模型中的能力值转换成共享效果。"""
    from .catalog import triggered_effects

    amounts = {(Event.TURN_START, EffectKind.GAIN_STRENGTH): per_turn_strength,
               (Event.ATTACK_RESOLVED, EffectKind.GAIN_BLOCK): block_per_attack}
    return tuple(replace(rule, amount=amounts[(rule.event, rule.action)])
                 for rule in triggered_effects() if amounts.get((rule.event, rule.action), 0))


def capability(effects: tuple[CardEffect, ...], event: Event, kind: EffectKind) -> int:
    """搜索只询问能力语义，无须判断具体牌名。"""
    return sum(e.amount for e in effects if isinstance(e, TriggeredEffect)
               and e.event is event and e.action is kind)
