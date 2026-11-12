"""独立竞技场使用的初始卡组和简单遭遇.

这些场景刻意保持简单, 数值不代表真实游戏.
它们用于让两个策略版本在相同战斗中比较, 后续可随策略完善增加场景.
"""

from __future__ import annotations

from .engine import DeckCard, Intent, Monster

STRIKE = DeckCard("打击", cost=1, damage=6, target="AnyEnemy")
DEFEND = DeckCard("防御", cost=1, block=5, target="Self")
NEUTRALIZE = DeckCard("中和", cost=0, damage=3, weak=1, target="AnyEnemy")
SURVIVOR = DeckCard("生存者", cost=1, block=8, target="Self")
BASH = DeckCard("痛击", cost=2, damage=8, vulnerable=2, target="AnyEnemy")


def starter(character: str = "silent") -> list[DeckCard]:
    character = character.lower()
    if character in ("ironclad", "铁甲战士"):
        return [STRIKE] * 5 + [DEFEND] * 4 + [BASH]
    # 静默猎手
    return [STRIKE] * 5 + [DEFEND] * 5 + [NEUTRALIZE, SURVIVOR]


def _nibbit() -> list[Monster]:
    return [Monster("小啃兽", hp=44, max_hp=44, script=[Intent("attack", 12)])]


def _slimes() -> list[Monster]:
    return [
        Monster("树叶史莱姆（小）", hp=11, max_hp=11, script=[Intent("attack", 3)]),
        Monster("树叶史莱姆（中）", hp=34, max_hp=34, script=[Intent("attack", 8)]),
        Monster("树枝史莱姆（小）", hp=10, max_hp=10, script=[Intent("attack", 4)]),
    ]


def _jaw_worm() -> list[Monster]:
    # 交替执行重击和强化, 用于近似前期较强敌人的战斗节奏.
    return [Monster("颚虫", hp=44, max_hp=44, script=[Intent("attack", 11), Intent("buff", strength=3)])]


def _lagavulin() -> list[Monster]:
    return [Monster("拉加维林", hp=109, max_hp=109,
                    script=[Intent("attack", 18), Intent("attack", 18), Intent("attack", 18)])]


ENCOUNTERS = {
    "nibbit": _nibbit,
    "slimes": _slimes,
    "jaw_worm": _jaw_worm,
    "lagavulin": _lagavulin,
}


def encounter(name: str) -> list[Monster]:
    try:
        return ENCOUNTERS[name]()
    except KeyError:
        raise SystemExit(f"unknown encounter {name!r}; known: {', '.join(sorted(ENCOUNTERS))}")
