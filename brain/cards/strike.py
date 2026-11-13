"""打击：供统一卡牌集提供旧模拟器已支持的数值。

数值保留旧模拟目录的图鉴摘录口径。
共享效果尚未迁移，仍通过 simulation_fields 交给现有引擎结算；不扩充支持范围。
"""

from dataclasses import dataclass

from .base import BaseCard, CardType


@dataclass(frozen=True)
class Strike(BaseCard):
    id = "STRIKE_IRONCLAD"
    base_name = "打击"
    type = CardType.ATTACK
    cost = 1
    known_versions = (False, True)
    effects = ()

    def simulation_fields(self) -> dict:
        return {"cost": self.cost,
                "damage": 9 if self.upgraded else 6}
