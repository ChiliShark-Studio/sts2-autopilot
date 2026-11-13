"""战斗共享效果结算：供动作搜索和完整战斗模拟器共同调用。

负责执行效果、登记和叠加生效状态、恢复及导出状态，并按战斗流程发出的事件触发和清理效果。
卡牌类声明具体规则；本文件不选择战斗动作，也不决定何时开始或结束回合。
"""

from dataclasses import dataclass, replace
from typing import Protocol

from ..cards.effects import CardEffect, Effect, EffectKind, Event, TriggeredEffect


@dataclass
class ActiveEffect:
    """本场生效实例：独立累计层数，并持有来源卡牌声明的规则。"""

    rule: TriggeredEffect
    amount: int


class EffectState(Protocol):
    strength: int
    block: int
    effect_powers: dict[str, ActiveEffect]


def read_powers(raw: dict[str, int]) -> dict[str, ActiveEffect]:
    """仅在状态输入边界兼容名称；执行时始终使用稳定 ID。"""
    from ..cards.catalog import triggered_effects

    return {rule.power_id: ActiveEffect(rule, raw.get(rule.power_id, raw.get(rule.name, 0)))
            for rule in triggered_effects() if raw.get(rule.power_id, raw.get(rule.name, 0))}


def power_amount(state: EffectState, identity: str) -> int:
    active = state.effect_powers.get(identity)
    return active.amount if active is not None else 0


def power_statuses(state: EffectState) -> list[dict]:
    return [{"id": identity, "name": active.rule.name, "amount": active.amount}
            for identity, active in state.effect_powers.items() if active.amount]


def apply_effects(state: EffectState, effects: tuple[CardEffect, ...]) -> None:
    for effect in effects:
        if isinstance(effect, TriggeredEffect):
            if (effect.action is not EffectKind.GAIN_BLOCK and effect.action is not EffectKind.GAIN_STRENGTH
                    or not isinstance(effect.event, Event)
                    or effect.expires is not None and not isinstance(effect.expires, Event)):
                raise ValueError(f"未支持的触发效果：{effect}")
            active = state.effect_powers.get(effect.power_id)
            if active is None:
                state.effect_powers[effect.power_id] = ActiveEffect(effect, effect.amount)
            else:
                if replace(active.rule, amount=effect.amount) != effect:
                    raise ValueError(f"同一状态 ID 存在不同的效果规则：{effect.power_id}")
                active.amount += effect.amount
        elif effect.kind is EffectKind.GAIN_STRENGTH:
            state.strength += effect.amount
        elif effect.kind is EffectKind.GAIN_BLOCK:
            # 状态产生的格挡不附加卡牌的敏捷、脆弱等修正。
            state.block += effect.amount
        else:
            raise ValueError(f"未支持的效果：{effect.kind}")


def emit(state: EffectState, event: Event) -> tuple[Effect, ...]:
    """战斗流程发出事件后，触发对应效果并清理到期状态。"""
    resolved = []
    for identity, active in tuple(state.effect_powers.items()):
        if active.rule.event is event:
            effect = Effect(active.rule.action, active.amount, identity)
            apply_effects(state, (effect,))
            resolved.append(effect)
        if active.rule.expires is event:
            del state.effect_powers[identity]
    return tuple(resolved)
