"""本局策略上下文：供地图、构筑、选牌和药水顾问共享同一个实例。

仅保存构筑计划、路线与删牌次数，由游戏循环在本局结束或回到菜单时重置；实际资源每次读取最新状态。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyContext:
    """仅保留本局的构筑判断与路线计划；实际资源始终读取最新状态。"""

    plan: str = ""
    # 开局规划出的路线, 每项是 (col, row); 为空表示需要在当前节点重新规划.
    route: tuple[tuple[int, int], ...] = ()
    route_note: str = ""
    # 本局已购买的删牌服务次数: 删牌价格随次数上涨.
    removals: int = 0

    def reset(self) -> None:
        self.plan = ""
        self.route = ()
        self.route_note = ""
        self.removals = 0
