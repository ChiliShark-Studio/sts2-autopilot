"""烙印：供统一卡牌集提供旧模拟器已支持的数值。

数值保留旧模拟目录的图鉴摘录口径。
共享效果尚未迁移，仍通过 simulation_fields 交给现有引擎结算；不扩充支持范围。
"""

from dataclasses import dataclass

from .base import BaseCard, CardType


@dataclass(frozen=True)
class Brand(BaseCard):
    id = "BRAND"
    base_name = "烙印"
    type = CardType.SKILL
    cost = 0
    known_versions = (False, True)
    effects = ()

    def simulation_fields(self) -> dict:
        return {"cost": self.cost,
                "hp_cost": 1,
                "strength": 2 if self.upgraded else 1}
