"""加载 JSON 战斗场景, 只接受模拟器已支持的字段和规则."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engine import DeckCard, Fight, Intent, Monster
from .special_intents import CUSTOM_INTENTS
from ..cards import find_card


def _object(value: Any, allowed: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{where} 包含不支持的字段: {', '.join(sorted(unknown))}")
    return value


def _items(value: Any, where: str) -> list:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where} 必须是非空数组")
    return value


def _int(value: Any, where: str, minimum: int | None = 0) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        bound = "整数" if minimum is None else f"大于等于 {minimum} 的整数"
        raise ValueError(f"{where} 必须是{bound}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 必须是非空字符串")
    return value


def _bool(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{where} 必须是布尔值")
    return value


def _status(value: Any, supported: dict[str, str], where: str) -> dict[str, int]:
    if not isinstance(value, list):
        raise ValueError(f"{where} 必须是数组")
    result = {}
    for index, raw in enumerate(value):
        entry = _object(raw, {"id", "name", "amount"}, f"{where}[{index}]")
        state_id = _text(entry.get("id"), f"{where}[{index}].id")
        if state_id not in supported:
            raise ValueError(f"{where} 不支持状态 {state_id}")
        field = supported[state_id]
        if field in result:
            raise ValueError(f"{where} 重复定义状态 {state_id}")
        result[field] = _int(entry.get("amount"), f"{where}[{index}].amount",
                             None if field in {"strength", "dexterity"} else 0)
    return result


@dataclass
class Scenario:
    name: str
    description: str
    player: dict[str, int]
    deck: list[DeckCard]
    phases: list[list[Monster]]
    seed: int
    max_turns: int
    # 录像场景: 重放时可以给定"当前手牌"和"已装备遗物", 不必依赖抽牌顺序.
    starting_hand: list[DeckCard] = field(default_factory=list)
    relics: list[dict[str, Any]] = field(default_factory=list)
    # 战斗类型 (monster / elite / boss): 战斗算法按它调整血量权重.
    screen: str = "monster"

    def create_fight(self, seed: int) -> Fight:
        """每次使用独立敌人实例, 避免不同种子之间共享战斗状态."""
        phases = copy.deepcopy(self.phases)
        return Fight(self.deck, phases[0], following_phases=phases[1:], seed=seed,
                     starting_hand=copy.deepcopy(self.starting_hand) or None,
                     screen=self.screen, **self.player)


def load(path: Path) -> Scenario:
    """读取场景, 未支持的字段或效果直接报错, 不默默降级."""
    raw = _object(json.loads(path.read_text(encoding="utf-8-sig")),
                  {"name", "description", "seed", "max_turns", "player", "enemies", "phases",
                   "relics", "screen"}, "场景")
    screen = _text(raw.get("screen", "monster"), "screen")
    if screen not in {"monster", "elite", "boss"}:
        raise ValueError("screen 只支持 monster, elite 或 boss")
    player = _object(raw.get("player"),
                     {"hp", "max_hp", "max_energy", "energy", "block", "draw_per_turn",
                      "status", "deck", "hand"},
                     "player")
    hp = _int(player.get("hp"), "player.hp", 1)
    max_hp = _int(player.get("max_hp", hp), "player.max_hp", 1)
    max_energy = _int(player.get("max_energy", 3), "player.max_energy", 1)
    energy = _int(player.get("energy", max_energy), "player.energy")
    if hp > max_hp or energy > max_energy:
        raise ValueError("初始血量和能量不能超过对应上限")
    initial = {"hp": hp, "max_hp": max_hp, "max_energy": max_energy, "energy": energy,
               "block": _int(player.get("block", 0), "player.block"),
               "draw_per_turn": _int(player.get("draw_per_turn", 5), "player.draw_per_turn", 1)}
    initial.update(_status(player.get("status", []),
                           {"STRENGTH_POWER": "strength", "DEXTERITY_POWER": "dexterity"}, "player.status"))

    numeric = {"cost", "damage", "hits", "block", "vulnerable", "weak", "draw", "energy",
               # 能力牌: 打出时立即获得的力量 (燃烧).
               "strength"}

    def cards(entries: Any, where: str) -> list[DeckCard]:
        """把 JSON 里的卡牌条目解析成模拟器的牌 (deck 与 hand 共用同一套字段)."""
        out: list[DeckCard] = []
        for index, entry in enumerate(_items(entries, where)):
            location = f"{where}[{index}]"
            card = _object(entry, numeric | {"name", "count", "target_type", "exhaust",
                                             "cost_x", "hits_x"}, location)
            raw_x = isinstance(card.get("cost"), str) and card["cost"].strip().upper() == "X"
            cost_x = _bool(card.get("cost_x", False), f"{location}.cost_x") or raw_x
            definition = find_card(str(card.get("name") or "")) if cost_x else None
            hits_x = _bool(card.get("hits_x", bool(definition and definition.hits_x)),
                           f"{location}.hits_x")
            if cost_x and not hits_x:
                raise ValueError(f"{location} 的 X 费效果尚未支持；当前只接入按 X 次重复攻击")
            values = {key: 0 if key == "cost" and raw_x else _int(value, f"{location}.{key}",
                                                                 1 if key == "hits" else 0)
                      for key, value in card.items() if key in numeric}
            default_target = ("AnyEnemy" if any(values.get(k, 0) for k in ("damage", "weak", "vulnerable"))
                              else "Self")
            if definition is not None:
                default_target = definition.simulation_fields().get("target", default_target)
            target = _text(card.get("target_type", default_target), f"{location}.target_type")
            if target not in {"AnyEnemy", "AllEnemies", "Self"}:
                raise ValueError(f"{location}.target_type 暂只支持 AnyEnemy、AllEnemies 或 Self")
            if target == "Self" and any(values.get(k, 0) for k in ("damage", "weak", "vulnerable")):
                raise ValueError(f"{location} 的伤害和敌方负面效果必须指定 AnyEnemy")
            template = DeckCard(name=_text(card.get("name"), f"{location}.name"), target=target,
                                cost_x=cost_x, hits_x=hits_x,
                                exhaust=_bool(card.get("exhaust", False), f"{location}.exhaust"),
                                **values)
            out.extend([template] * _int(card.get("count", 1), f"{location}.count", 1))
        return out

    deck = cards(player.get("deck"), "player.deck")
    starting_hand = cards(player["hand"], "player.hand") if "hand" in player else []

    # 遗物: 只接受模拟器真的能生效的效果, 其余报错而不是当没有.
    relics: list[dict[str, Any]] = []
    for index, entry in enumerate(raw.get("relics") or []):
        where = f"relics[{index}]"
        relic = _object(entry, {"name", "description", "effects"}, where)
        effects = _object(relic.get("effects") or {},
                          {"starting_strength", "starting_block", "first_block_double"},
                          f"{where}.effects")
        applied: dict[str, Any] = {}
        if "starting_strength" in effects:
            value = _int(effects["starting_strength"], f"{where}.effects.starting_strength")
            initial["strength"] = initial.get("strength", 0) + value
            applied["starting_strength"] = value
        if "starting_block" in effects:
            value = _int(effects["starting_block"], f"{where}.effects.starting_block")
            initial["block"] += value
            applied["starting_block"] = value
        if "first_block_double" in effects:
            flag = _bool(effects["first_block_double"], f"{where}.effects.first_block_double")
            initial["relic_first_block_double"] = flag
            applied["first_block_double"] = flag
        relics.append({"name": _text(relic.get("name"), f"{where}.name"),
                       "description": relic.get("description", ""), "effects": applied})

    if ("enemies" in raw) == ("phases" in raw):
        raise ValueError("场景必须且只能定义 enemies 或 phases 之一")
    phase_entries = raw.get("phases") if "phases" in raw else [{"enemies": raw["enemies"]}]
    phases = []
    entity_ids = set()
    for phase_index, entry in enumerate(_items(phase_entries, "phases")):
        phase = _object(entry, {"name", "enemies"}, f"phases[{phase_index}]")
        monsters = []
        for index, entry in enumerate(_items(phase.get("enemies"), f"phases[{phase_index}].enemies")):
            where = f"phases[{phase_index}].enemies[{index}]"
            enemy = _object(entry, {"entity_id", "name", "hp", "max_hp", "block", "status",
                                    "repeat", "repeat_from", "intents"}, where)
            next_id = len(entity_ids)
            while f"E{next_id}" in entity_ids:
                next_id += 1
            entity_id = _text(enemy.get("entity_id", f"E{next_id}"), f"{where}.entity_id")
            if entity_id in entity_ids:
                raise ValueError(f"敌人 entity_id 重复: {entity_id}")
            entity_ids.add(entity_id)
            script = []
            for turn, entry in enumerate(_items(enemy.get("intents"), f"{where}.intents")):
                location = f"{where}.intents[{turn}]"
                intent = _object(entry, {"type", "damage", "hits", "block", "strength", "handler", "params"}, location)
                kind = _text(intent.get("type"), f"{location}.type")
                if kind not in {"attack", "defend", "buff", "wait", "custom"}:
                    raise ValueError(f"{location}.type 只支持 attack, defend, buff, wait, custom")
                if (kind != "attack" and ("damage" in intent or "hits" in intent)
                        or kind != "defend" and "block" in intent):
                    raise ValueError(f"{location} 的伤害或格挡字段与意图类型不符")
                handler = ""
                params = {}
                if kind == "custom":
                    handler = _text(intent.get("handler"), f"{location}.handler")
                    if handler not in CUSTOM_INTENTS:
                        raise ValueError(f"{location} 未注册特殊意图 {handler}")
                    params = intent.get("params")
                    CUSTOM_INTENTS[handler]["validate"](params)
                elif "handler" in intent or "params" in intent:
                    raise ValueError(f"{location} 只有 custom 意图可以指定 handler 和 params")
                script.append(Intent(kind,
                                     damage=_int(intent.get("damage", 0), f"{location}.damage"),
                                     hits=_int(intent.get("hits", 1), f"{location}.hits", 1),
                                     block=_int(intent.get("block", 0), f"{location}.block"),
                                     strength=_int(intent.get("strength", 0), f"{location}.strength", None),
                                     handler=handler, params=params))
            repeat = _bool(enemy.get("repeat", False), f"{where}.repeat")
            repeat_from = _int(enemy.get("repeat_from", 0), f"{where}.repeat_from")
            if repeat_from >= len(script) or (not repeat and repeat_from != 0):
                raise ValueError(f"{where}.repeat_from 必须指向循环脚本中的有效位置")
            enemy_hp = _int(enemy.get("hp"), f"{where}.hp", 1)
            enemy_max = _int(enemy.get("max_hp", enemy_hp), f"{where}.max_hp", 1)
            if enemy_hp > enemy_max:
                raise ValueError(f"{where}.hp 不能超过 max_hp")
            powers = _status(enemy.get("status", []), {"STRENGTH_POWER": "strength",
                             "VULNERABLE_POWER": "vulnerable", "WEAK_POWER": "weak",
                             # 硬化外壳: 每回合掉血上限; 初始额度由 Monster 同步.
                             "HARDENED_SHELL_POWER": "hp_loss_cap",
                             # 胆小: 每回合第一次被命中时获得的格挡.
                             "SKITTISH_POWER": "skittish"}, f"{where}.status")
            monsters.append(Monster(name=_text(enemy.get("name"), f"{where}.name"), hp=enemy_hp,
                                    max_hp=enemy_max, block=_int(enemy.get("block", 0), f"{where}.block"),
                                    script=script, entity_id=entity_id, repeat=repeat,
                                    repeat_from=repeat_from, kind=screen, **powers))
        phases.append(monsters)
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValueError("description 必须是字符串")
    return Scenario(name=_text(raw.get("name"), "name"), description=description,
                    player=initial, deck=deck, phases=phases,
                    seed=_int(raw.get("seed", 0), "seed"),
                    max_turns=_int(raw.get("max_turns", 60), "max_turns", 1),
                    starting_hand=starting_hand, relics=relics, screen=screen)
