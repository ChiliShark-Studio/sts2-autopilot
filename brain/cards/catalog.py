"""卡牌注册与查找：按各子类已知版本提供实例，并派生模拟数值目录。

注册表只维护类型映射，不另存牌面数值；缺少升级数据的牌不生成升级版。
"""

from functools import lru_cache

from .base import BaseCard
from .demon_form import DemonForm
from .rage import Rage
from .strike import Strike
from .defend import Defend
from .bash import Bash
from .uppercut import Uppercut
from .tremble import Tremble
from .shrug_it_off import ShrugItOff
from .twin_strike import TwinStrike
from .ricochet import Ricochet
from .sword_boomerang import SwordBoomerang
from .whirlwind import Whirlwind
from .body_slam import BodySlam
from .bully import Bully
from .ashen_strike import AshenStrike
from .inflame import Inflame
from .brand import Brand
from .dark_embrace import DarkEmbrace
from .thunderclap import Thunderclap
from .armaments import Armaments
from .setup_strike import SetupStrike
from .headbutt import Headbutt
from .burning_pact import BurningPact
from .true_grit import TrueGrit
from .rupture import Rupture
from .taunt import Taunt
from .pillage import Pillage
from .breakthrough import Breakthrough
from .rampage import Rampage
from .dismantle import Dismantle
from .pommel_strike import PommelStrike
from .iron_wave import IronWave
from .corruption import Corruption
from .feel_no_pain import FeelNoPain
from .barricade import Barricade
from .juggernaut import Juggernaut
from .unmovable import Unmovable
from .legacy_rupture import LegacyRupture
from .cruelty import Cruelty
from .pact_finale import PactFinale
from .effects import TriggeredEffect


_CARD_TYPES: dict[str, type[BaseCard]] = {card.id: card for card in (
    Rage, DemonForm,
    Strike,
    Defend,
    Bash,
    Uppercut,
    Tremble,
    ShrugItOff,
    TwinStrike,
    Ricochet,
    SwordBoomerang,
    Whirlwind,
    BodySlam,
    Bully,
    AshenStrike,
    Inflame,
    Brand,
    DarkEmbrace,
    Thunderclap,
    Armaments,
    SetupStrike,
    Headbutt,
    BurningPact,
    TrueGrit,
    Rupture,
    Taunt,
    Pillage,
    Breakthrough,
    Rampage,
    Dismantle,
    PommelStrike,
    IronWave,
    Corruption,
    FeelNoPain,
    Barricade,
    Juggernaut,
    Unmovable,
    LegacyRupture,
    Cruelty,
    PactFinale,
)}


@lru_cache(maxsize=1)
def definitions() -> tuple[BaseCard, ...]:
    return tuple(card(upgraded=upgraded) for card in _CARD_TYPES.values() for upgraded in card.known_versions)


def simulation_catalog() -> dict[str, dict]:
    """从卡牌子类派生模拟数值目录；调用方修改结果不会影响卡牌定义。"""
    return {card.name: card.simulation_fields() for card in definitions()}


def triggered_effects() -> tuple[TriggeredEffect, ...]:
    """从卡牌类取得恢复状态所需的规则，不另存一份业务规则表。"""
    return tuple(effect for card in definitions() if not card.upgraded
                 for effect in card.effects if isinstance(effect, TriggeredEffect))


def get_card(identity: str, *, upgraded: bool = False) -> BaseCard:
    try:
        card_class = _CARD_TYPES[identity]
    except KeyError:
        raise KeyError(f"卡牌集中未收录：{identity}") from None
    if upgraded not in card_class.known_versions:
        raise KeyError(f"卡牌版本尚未收录：{identity}{'+' if upgraded else ''}")
    return card_class(upgraded=upgraded)


def find_card(identifier: str, *, upgraded: bool = False) -> BaseCard | None:
    """仅在输入边界接受名称；运行时实例统一使用稳定 ID。"""
    upgraded = upgraded or identifier.endswith("+")
    identifier = identifier.rstrip("+")
    for card in definitions():
        if card.upgraded == upgraded and identifier in {card.id, card.base_name}:
            return card
    return None
