"""恶魔形态：从下回合开始，每回合获得力量。"""

from dataclasses import dataclass

from .base import BaseCard, CardType
from .effects import EffectKind, Event, TriggeredEffect


@dataclass(frozen=True)
class DemonForm(BaseCard):
    id = "DEMON_FORM"
    base_name = "恶魔形态"
    type = CardType.POWER
    cost = 3

    @property
    def effects(self) -> tuple[TriggeredEffect, ...]:
        return (TriggeredEffect(power_id="DEMON_FORM_POWER", name=self.base_name,
                                event=Event.TURN_START, action=EffectKind.GAIN_STRENGTH,
                                amount=3 if self.upgraded else 2),)
