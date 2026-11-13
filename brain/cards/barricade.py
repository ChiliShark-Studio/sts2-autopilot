"""壁垒：供统一卡牌集提供旧模拟器已支持的数值。

数值保留旧模拟目录的社区快照；游戏 ID 尚未核对，沿用原名称标识。
共享效果尚未迁移，仍通过 simulation_fields 交给现有引擎结算；不扩充支持范围。
"""

from dataclasses import dataclass

from .base import BaseCard, CardType


@dataclass(frozen=True)
class Barricade(BaseCard):
    id = "壁垒"
    base_name = "壁垒"
    type = CardType.POWER
    cost = 3
    known_versions = (False,)
    effects = ()

    def simulation_fields(self) -> dict:
        return {"cost": self.cost,
                "keep_block": True}
