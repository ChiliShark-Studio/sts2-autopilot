"""旋风斩：消耗出牌时的剩余能量，对所有敌人造成 X 次伤害。

每次伤害为基础版 5、升级版 8；X 在战斗分支实际出牌时计算，零能量对应零次命中。
数值通过 simulation_fields 提供，动态费用与命中次数由战斗侧统一结算。
"""

from dataclasses import dataclass

from .base import BaseCard, CardType


@dataclass(frozen=True)
class Whirlwind(BaseCard):
    id = "WHIRLWIND"
    base_name = "旋风斩"
    type = CardType.ATTACK
    cost = 0
    cost_x = True
    hits_x = True
    known_versions = (False, True)
    effects = ()

    def simulation_fields(self) -> dict:
        return {"cost": self.cost,
                "cost_x": self.cost_x,
                "hits_x": self.hits_x,
                "damage": 8 if self.upgraded else 5,
                "hits": 1,
                "target": "AllEnemies"}
