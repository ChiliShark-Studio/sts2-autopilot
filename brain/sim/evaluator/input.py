"""评估输入：显式数值优先，未知效果不得静默丢弃。"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from ...model import Card
from ...cards import find_card
from ...upgrades import upgrade
from ..engine import DeckCard
from ..ironclad import ALL_CARDS


def integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} 必须是大于等于 {minimum} 的整数")
    return value


def schedule(value: Any, turns: int, name: str) -> list[int]:
    if type(value) is int:
        return [integer(value, name)] * turns
    if not isinstance(value, list) or len(value) != turns:
        raise ValueError(f"{name} 必须是整数或长度等于 turns 的整数数组")
    return [integer(v, name) for v in value]


@dataclass
class EvaluationCard(DeckCard):
    """仅供独立工具结算的已核对效果。"""

    draw_per_vulnerable: int = 0
    topdeck: int = 0
    dexterity: int = 0
    temporary_strength: int = 0
    per_turn_energy: int = 0
    energy_per_attack: int = 0
    no_energy_gain: bool = False
    hellraiser: bool = False
    panache: int = 0
    per_strike: int = 0
    strike: bool = False
    all_enemies: bool = False


@dataclass
class EvaluationInput:
    deck: list[DeckCard]
    types: dict[str, str]
    turns: int
    runs: int
    seed: int
    hp: int
    enemy_hp: int
    incoming: list[int]
    energy: list[int]
    draw: list[int]
    strength: int
    block: int
    carry_energy: bool
    key_cards: list[str]
    groups: dict[str, list[str]]
    missing: list[str]
    assumptions: list[str]
    character: str
    relics: list[dict]
    upgrades: dict[str, DeckCard]


# 这些目录条目含当前引擎尚未忠实建模的额外机制，不能只取其中的伤害数值。
_UNSUPPORTED_CATALOG = {
    "旋风斩", "连续反弹", "飞剑回旋镖", "闪电霹雳", "突破", "头槌",
    "燃烧契约", "坚毅", "劫掠", "暴走", "拆卸", "契约终章",
}
_POWER_FIELDS = {
    "per_turn_strength", "block_per_exhaust", "draw_per_exhaust", "keep_block",
    "juggernaut", "double_first_block", "rupture", "cruelty", "corruption", "draw_per_vulnerable",
    "per_turn_energy", "hellraiser", "panache", "dexterity",
}

# 凶恶取自本地卡牌资料；深谋远虑取自 ringed_turn.json，去除当回合附加的昏眩。
_LOCAL_CARDS = {
    "武装+": {"cost": 1, "block": 5},
    "凶恶": {"cost": 1, "draw_per_vulnerable": 1},
    "深谋远虑": {"cost": 0, "draw": 2, "topdeck": 1, "exhaust": True},
    # 数值按 Spire Codex 稳定版接口的本次快照核对，来源和版本限制见 README。
    "完美打击": {"cost": 2, "damage": 6, "per_strike": 2},
    "预备打击": {"cost": 1, "damage": 7, "temporary_strength": 2},
    "跃跃欲试": {"cost": 2, "energy_per_attack": 1, "no_energy_gain": True},
    "究极打击": {"cost": 1, "damage": 14},
    "薪火之源": {"cost": 2, "per_turn_energy": 1,
                  "description": "在回合开始时，获得1点能量。"},
    "地狱狂徒": {"cost": 2, "hellraiser": True},
    "神气制胜": {"cost": 0, "panache": 10},
    "非凡技艺": {"cost": 1, "strength": 1, "dexterity": 1},
    "焚烧": {"cost": 1, "damage": 2, "hits": 4, "all_enemies": True},
}
_STRIKE_NAMES = {"打击", "双重打击", "剑柄打击", "完美打击", "预备打击", "究极打击"}

# 仅适配日志中已核对的战外效果，不把未知遗物当作零收益。
_OUTSIDE_COMBAT_RELICS = {
    "BURNING_BLOOD": ("在战斗结束时，回复6点生命。", "战后回复未计入本场战斗过程及胜利剩余血量"),
    "POMANDER": ("拾起时，升级一张牌。", "拾取升级已反映在输入牌组，不再次升级"),
}


def _upgrades(deck: list[DeckCard], missing: list[str]) -> dict[str, DeckCard]:
    if not any(c.name.rstrip("+") == "武装" for c in deck):
        return {}
    result = {}
    keys = ("cost", "damage", "hits", "block", "vulnerable", "weak", "draw", "energy")
    for card in deck:
        if card.name.endswith("+"):
            continue
        native = Card(id=card.id, name=card.name, type=card_type(card),
                      **{key: getattr(card, key) for key in keys})
        upgraded = upgrade(native)
        if upgraded is native:
            missing.append(f"武装无法完整评估：{card.name} 的升级规则尚未覆盖")
            continue
        result[card.id] = replace(card, name=upgraded.name, **{k: getattr(upgraded, k) for k in keys})
    return result


def card_type(card: DeckCard, explicit: str = "") -> str:
    if explicit:
        return explicit
    if card.definition is not None:
        return card.definition.type.value
    if any(getattr(card, key, 0) for key in _POWER_FIELDS) or (
            card.strength and not card.hp_cost and not card.damage):
        return "power"
    return "attack" if card.damage or card.scales_with_block or getattr(card, "per_strike", 0) else "skill"


def parse(raw: dict) -> EvaluationInput:
    if not isinstance(raw, dict):
        raise ValueError("输入必须是对象")
    allowed = {"deck", "character", "turns", "runs", "seed", "hp", "enemy_hp", "incoming_by_turn",
               "base_energy", "energy_by_turn", "draw_by_turn", "relics", "key_cards", "startup_groups"}
    if raw.keys() - allowed:
        raise ValueError(f"不支持的输入字段：{sorted(raw.keys() - allowed)}")
    turns = integer(raw.get("turns", 5), "turns", 1)
    runs = integer(raw.get("runs", 100), "runs", 1)
    if turns > 50 or runs > 1000:
        raise ValueError("评估预算不得超过 50 回合、1000 个种子")
    missing, assumptions, deck, types = [], [], [], {}
    if not isinstance(raw.get("deck"), list) or not raw["deck"]:
        raise ValueError("deck 必须是非空数组")
    numeric = {f.name for f in fields(EvaluationCard)} - {
        "name", "id", "description", "target", "exhaust", "playable", "keep_block",
        "double_first_block", "corruption", "scales_with_block", "cruelty", "no_energy_gain",
        "hellraiser", "strike", "all_enemies", "definition"}
    booleans = {"exhaust", "playable", "keep_block", "double_first_block", "corruption", "scales_with_block",
                "no_energy_gain", "hellraiser", "strike", "all_enemies"}
    allowed_card = {f.name for f in fields(EvaluationCard)} - {"definition"} | {"count", "type"}
    for index, entry in enumerate(raw["deck"]):
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            raise ValueError(f"deck[{index}] 必须是牌名或对象")
        unknown = entry.keys() - allowed_card
        missing.extend(f"deck[{index}] 未支持字段 {key}" for key in sorted(unknown))
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"deck[{index}].name 必须是非空字符串")
        spec = {k: v for k, v in entry.items() if k in allowed_card - {"count", "type"}}
        definition = None
        if set(spec) == {"name"}:
            catalog = {**ALL_CARDS, **_LOCAL_CARDS}
            definition = find_card(name)
            if definition is not None:
                name = spec["name"] = definition.name
            if name not in catalog:
                missing.append(f"未收录卡牌：{name}，请提供显式数值与效果")
            elif name.rstrip("+") in _UNSUPPORTED_CATALOG:
                missing.append(f"{name} 含尚未支持的目标、选牌或条件机制")
            else:
                spec.update(catalog[name])
                assumptions.append("按牌名使用仓库现有铁甲战士目录，数值仍需与本机版本核对")
        elif entry.get("description"):
            missing.append(f"{name} 的描述文本尚未核实是否被显式效果完整覆盖")
        for key in numeric & spec.keys():
            integer(spec[key], f"{name}.{key}", 1 if key == "hits" else 0)
        for key in booleans & spec.keys():
            if type(spec[key]) is not bool:
                raise ValueError(f"{name}.{key} 必须是布尔值")
        if "cruelty" in spec and (type(spec["cruelty"]) not in (int, float) or not 0 <= spec["cruelty"] <= 1):
            raise ValueError(f"{name}.cruelty 必须在 0 到 1 之间")
        if not isinstance(entry.get("type", ""), str):
            raise ValueError(f"{name}.type 必须是字符串")
        kind = entry.get("type", "").lower()
        if kind not in {"", "attack", "skill", "power", "status", "curse"}:
            raise ValueError(f"{name}.type 无效")
        if spec.get("target", "AnyEnemy") not in {"AnyEnemy", "Self", "None"}:
            missing.append(f"{name} 的目标机制未支持：{spec['target']}")
        if kind in {"status", "curse"}:
            spec["playable"] = False
        spec.setdefault("target", "AnyEnemy" if any(spec.get(k) for k in (
            "damage", "scales_with_block", "per_vulnerable", "vulnerable", "weak")) else "Self")
        if spec.get("topdeck") and not spec.get("draw"):
            raise ValueError(f"{name} 的放回牌堆效果目前需要先抽牌")
        spec.setdefault("strike", name.rstrip("+") in _STRIKE_NAMES)
        template = EvaluationCard(**spec, definition=definition)
        count = integer(entry.get("count", 1), f"{name}.count", 1)
        if len(deck) + count > 100:
            raise ValueError("牌组不得超过 100 张")
        for _ in range(count):
            identity = f"eval_{len(deck)}"
            template = replace(template, id=identity)
            deck.append(template)
            types[identity] = card_type(template, kind)
    base = integer(raw.get("base_energy", 3), "base_energy")
    energy = schedule(raw.get("energy_by_turn", base), turns, "energy_by_turn")
    draw = schedule(raw.get("draw_by_turn", 5), turns, "draw_by_turn")
    if "energy_by_turn" not in raw and "base_energy" not in raw:
        assumptions.append("基础每回合能量暂按 3 点；需要计入遗物时请提供资源输入")
    if "draw_by_turn" not in raw:
        assumptions.append("每回合基础抽牌暂按 5 张")
    relics = raw.get("relics", [])
    if not isinstance(relics, list):
        raise ValueError("relics 必须是数组")
    strength = block = 0
    carry = False
    for relic in relics:
        if not isinstance(relic, dict) or not isinstance(relic.get("name"), str):
            raise ValueError("每个遗物必须包含 name")
        outside = _OUTSIDE_COMBAT_RELICS.get(relic.get("id"))
        if (outside and "effects" not in relic and relic.get("description") == outside[0]
                and not relic.keys() - {"id", "name", "description", "counter", "keywords"}):
            assumptions.append(f"遗物 {relic['name']}：{outside[1]}")
            continue
        missing.extend(f"遗物 {relic['name']} 未支持字段 {key}"
                       for key in sorted(relic.keys() - {"name", "description", "effects"}))
        effects = relic.get("effects")
        if not isinstance(effects, dict) or not effects:
            missing.append(f"遗物 {relic['name']} 缺少明确的 effects，不能按无效果计算")
            continue
        supported = {"energy_by_turn", "draw_by_turn", "starting_strength", "starting_block", "carry_energy"}
        missing.extend(f"遗物 {relic['name']} 未支持效果 {k}" for k in sorted(effects.keys() - supported))
        for field, target in (("energy_by_turn", energy), ("draw_by_turn", draw)):
            if field in effects:
                if field in raw:
                    raise ValueError(f"{field} 已包含外部总量，不能再重复叠加遗物 {field}")
                for i, amount in enumerate(schedule(effects[field], turns, field)):
                    target[i] += amount
        strength += integer(effects.get("starting_strength", 0), "starting_strength")
        block += integer(effects.get("starting_block", 0), "starting_block")
        if type(effects.get("carry_energy", False)) is not bool:
            raise ValueError("carry_energy 必须是布尔值")
        carry = carry or effects.get("carry_energy", False)
    keys = raw.get("key_cards", [c.name for c in deck if types[c.id] == "power"])
    groups = raw.get("startup_groups", {})
    if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
        raise ValueError("key_cards 必须是牌名数组")
    if not isinstance(groups, dict) or any(not isinstance(v, list) or not v or
            any(not isinstance(k, str) for k in v) for v in groups.values()):
        raise ValueError("startup_groups 必须是组合名到非空牌名数组的映射")
    names = {c.name for c in deck}
    if set(keys).union(*(set(v) for v in groups.values())) - names:
        raise ValueError("指定的关键牌或启动组合含牌组中不存在的牌")
    character = raw.get("character", "IRONCLAD")
    if character not in {"IRONCLAD", "ironclad", "铁甲战士"}:
        missing.append(f"角色 {character} 的专属规则尚未覆盖")
    assumptions.append("默认敌人为单体木桩；攻击时间表是受控基准，不是真实精英或 Boss")
    assumptions.append("只执行显式输入的效果；角色名不会自动附加起始遗物、战后治疗或其他专属机制")
    upgrades = _upgrades(deck, missing)
    return EvaluationInput(deck, types, turns, runs, integer(raw.get("seed", 0), "seed"),
                           integer(raw.get("hp", 80), "hp", 1),
                           integer(raw.get("enemy_hp", 100000), "enemy_hp", 1),
                           schedule(raw.get("incoming_by_turn", 10), turns, "incoming_by_turn"),
                           energy, draw, strength, block, carry, sorted(set(keys)), groups,
                           sorted(set(missing)), sorted(set(assumptions)), character, relics, upgrades)
