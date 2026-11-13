"""卡牌注册与查找；行为和数值由各卡牌子类提供。"""

from functools import lru_cache

from .base import BaseCard
from .demon_form import DemonForm
from .rage import Rage
from .effects import TriggeredEffect


_CARD_TYPES: dict[str, type[BaseCard]] = {card.id: card for card in (Rage, DemonForm)}


@lru_cache(maxsize=1)
def definitions() -> tuple[BaseCard, ...]:
    return tuple(card(upgraded=upgraded) for card in _CARD_TYPES.values() for upgraded in (False, True))


def triggered_effects() -> tuple[TriggeredEffect, ...]:
    """从卡牌类取得恢复状态所需的规则，不另存一份业务规则表。"""
    return tuple(effect for card in definitions() if not card.upgraded
                 for effect in card.effects if isinstance(effect, TriggeredEffect))


def get_card(identity: str, *, upgraded: bool = False) -> BaseCard:
    try:
        card_class = _CARD_TYPES[identity]
    except KeyError:
        raise KeyError(f"卡牌集中未收录：{identity}") from None
    return card_class(upgraded=upgraded)


def find_card(identifier: str, *, upgraded: bool = False) -> BaseCard | None:
    """仅在输入边界接受名称；运行时实例统一使用稳定 ID。"""
    upgraded = upgraded or identifier.endswith("+")
    identifier = identifier.rstrip("+")
    for card in definitions():
        if card.upgraded == upgraded and identifier in {card.id, card.base_name}:
            return card
    return None
