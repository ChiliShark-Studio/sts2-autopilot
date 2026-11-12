"""已注册的特殊意图: 参数校验, 只读预览和实际执行各自独立."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .engine import DeckCard, Fight, Monster


INFECTION = DeckCard(
    name="感染", id="INFECTION", cost=0, damage=3, target="None", playable=False,
    end_turn_damage=3,
    description="不能被打出. 在你的回合结束时, 如果这张牌在你的手牌中, 你受到3点伤害.",
)


def validate_infection(params: Any) -> None:
    """只接受明确的数量和目标牌堆, 在加载文件时发现配置错误."""
    if not isinstance(params, dict) or params.keys() != {"count", "pile"}:
        raise ValueError("add_infection.params 必须且只能包含 count 和 pile")
    if type(params["count"]) is not int or params["count"] < 1:
        raise ValueError("add_infection.count 必须是大于等于 1 的整数")
    if params["pile"] not in ("draw_pile", "discard_pile"):
        raise ValueError("add_infection.pile 只支持 draw_pile 或 discard_pile")


def preview_infection(fight: Fight, monster: Monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    """返回意图信息, 不修改状态或消耗随机数."""
    pile = "抽牌堆" if params["pile"] == "draw_pile" else "弃牌堆"
    return [{"type": "StatusCard", "label": str(params["count"]),
             "description": f"将 {params['count']} 张感染加入{pile}."}]


def execute_infection(fight: Fight, monster: Monster, params: dict[str, Any]) -> None:
    """向弃牌堆追加感染, 或使用本场随机源将感染随机插入抽牌堆."""
    pile = fight.draw_pile if params["pile"] == "draw_pile" else fight.discard
    for _ in range(params["count"]):
        card = replace(INFECTION)
        if params["pile"] == "draw_pile":
            pile.insert(fight.rng.randrange(len(pile) + 1), card)
        else:
            pile.append(card)


CUSTOM_INTENTS = {
    "add_infection": {
        "validate": validate_infection,
        "preview": preview_infection,
        "execute": execute_infection,
    },
}
