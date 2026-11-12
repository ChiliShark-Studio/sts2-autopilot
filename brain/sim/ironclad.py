"""铁甲战士卡牌目录: 供模拟器构造牌组.

数值来源分两类, 都在 ``data/cards/ironclad.json`` 与注释里标明:
  * 游戏内图鉴抓到的牌 (本档已解锁), 数值与描述可直接核对;
  * 本档尚未解锁的能力牌, 按社区 wiki/构筑指南录入, 标了「社区数据」.

卡牌名字用中文 (桥接的状态里只有名字), 升级版用 ``+`` 后缀.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .engine import DeckCard

DATA_PATH = os.path.join("data", "cards", "ironclad.json")

# 基础牌: 游戏内图鉴 (data/cards/ironclad.json) 抓取.
BASE_CARDS: dict[str, dict] = {
    "打击": {"cost": 1, "damage": 6},
    "打击+": {"cost": 1, "damage": 9},
    "防御": {"cost": 1, "block": 5},
    "防御+": {"cost": 1, "block": 8},
    "痛击": {"cost": 2, "damage": 8, "vulnerable": 2},
    "痛击+": {"cost": 2, "damage": 10, "vulnerable": 3},
    "上勾拳": {"cost": 2, "damage": 13, "weak": 1, "vulnerable": 1},
    "战栗": {"cost": 1, "vulnerable": 3, "exhaust": True},
    "耸肩无视": {"cost": 1, "block": 8, "draw": 1},
    "耸肩无视+": {"cost": 1, "block": 11, "draw": 1},
    "双重打击": {"cost": 1, "damage": 5, "hits": 2},
    "连续反弹": {"cost": 2, "damage": 3, "hits": 4},
    "飞剑回旋镖": {"cost": 1, "damage": 3, "hits": 3},
    # X 费牌在引擎里没有 X, 按 2 费结算.
    "旋风斩": {"cost": 2, "damage": 5, "hits": 2},
    "旋风斩+": {"cost": 2, "damage": 8, "hits": 2},
    "全身撞击": {"cost": 1, "scales_with_block": True},
    "欺凌": {"cost": 0, "damage": 4, "per_vulnerable": 2},
    "灰烬打击": {"cost": 1, "damage": 6, "per_exhaust": 3},
    # ---- 能力牌: 图鉴抓到的部分 ----
    "恶魔形态": {"cost": 3, "per_turn_strength": 2},
    "恶魔形态+": {"cost": 3, "per_turn_strength": 3},
    "燃烧": {"cost": 1, "strength": 2},
    "燃烧+": {"cost": 1, "strength": 3},
    "烙印": {"cost": 0, "hp_cost": 1, "strength": 1},
    "烙印+": {"cost": 0, "hp_cost": 1, "strength": 2},
    "黑暗之拥": {"cost": 2, "draw_per_exhaust": 1},
    # ---- 其他常用牌: 同样来自游戏内图鉴 ----
    "闪电霹雳": {"cost": 1, "damage": 4, "vulnerable": 1},
    "武装": {"cost": 1, "block": 5},
    "预备打击": {"cost": 1, "damage": 7, "strength": 2},
    "头槌": {"cost": 1, "damage": 9},
    "燃烧契约": {"cost": 1, "draw": 2, "exhaust": True},
    "坚毅": {"cost": 1, "block": 7},
    "坚毅+": {"cost": 1, "block": 9},
    "撕裂": {"cost": 1, "rupture": 1},
    "挑衅": {"cost": 1, "block": 7, "vulnerable": 1},
    "劫掠": {"cost": 1, "damage": 6, "draw": 1},
    "突破": {"cost": 1, "damage": 9, "hp_cost": 1},
    "暴走": {"cost": 1, "damage": 9},
    "拆卸": {"cost": 1, "damage": 8},
    "剑柄打击": {"cost": 1, "damage": 9, "draw": 1},
    "铁斩波": {"cost": 1, "damage": 5, "block": 5},
}

# 本档尚未解锁, 数值来自社区构筑指南 (sts2front Ironclad Builds, 2026-03 校验).
COMMUNITY_CARDS: dict[str, dict] = {
    "腐化": {"cost": 3, "corruption": True},
    "无痛": {"cost": 1, "block_per_exhaust": 3},
    "壁垒": {"cost": 3, "keep_block": True},
    "主宰": {"cost": 2, "juggernaut": 5},
    "不可动摇": {"cost": 2, "double_first_block": True},
    "破裂": {"cost": 1, "rupture": 1},
    "破裂+": {"cost": 1, "rupture": 2},
    "残酷": {"cost": 1, "cruelty": 0.25},
    "残酷+": {"cost": 1, "cruelty": 0.50},
    "契约终章": {"cost": 0, "damage": 17, "exhaust": True},
}

ALL_CARDS: dict[str, dict] = {**BASE_CARDS, **COMMUNITY_CARDS}


def load_game_data() -> dict:
    """读取游戏内图鉴抓下来的原始数据 (可能不存在, 测试里用来交叉校验)."""
    if not os.path.exists(DATA_PATH):
        return {}
    with open(DATA_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def card(name: str, upgraded: bool = False) -> DeckCard:
    key = name if not upgraded else f"{name}+"
    spec = ALL_CARDS.get(key)
    if spec is None:
        raise KeyError(f"未收录的卡牌: {name}{'+' if upgraded else ''}")
    fields = dict(spec)
    fields.setdefault("target", "Self" if fields.get("block") and not fields.get("damage") else "AnyEnemy")
    return DeckCard(name=key, id=key, **fields)


def deck(spec: dict[str, int]) -> list[DeckCard]:
    """按 ``{牌名: 张数}`` 构造牌组."""
    out: list[DeckCard] = []
    for name, count in spec.items():
        out.extend(card(name) for _ in range(count))
    return out


def missing_for_game_data() -> list[str]:
    """列出仍在用社区数据、尚未被游戏图鉴核对的牌."""
    known = load_game_data()
    return sorted(n for n in COMMUNITY_CARDS if n.rstrip("+") not in known)
