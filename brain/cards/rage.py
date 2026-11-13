"""狂怒：本回合每张攻击牌结算后获得格挡。"""

from dataclasses import dataclass

from .base import BaseCard, CardType
from .effects import EffectKind, Event, TriggeredEffect


@dataclass(frozen=True)
class Rage(BaseCard):
    id = "RAGE"
    base_name = "狂怒"
    type = CardType.SKILL
    cost = 0

    @property
    def effects(self) -> tuple[TriggeredEffect, ...]:
        return (TriggeredEffect(power_id="RAGE_POWER", name=self.base_name,
                                event=Event.ATTACK_RESOLVED, action=EffectKind.GAIN_BLOCK,
                                amount=5 if self.upgraded else 3, expires=Event.TURN_END),)
