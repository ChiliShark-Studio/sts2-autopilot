"""加载 JSON 战斗场景, 只接受模拟器已支持的字段和规则."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine import DeckCard, Fight, Intent, Monster
from .special_intents import CUSTOM_INTENTS


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

    def create_fight(self, seed: int) -> Fight:
        """每次使用独立敌人实例, 避免不同种子之间共享战斗状态."""
        phases = copy.deepcopy(self.phases)
        return Fight(self.deck, phases[0], following_phases=phases[1:], seed=seed, **self.player)


def load(path: Path) -> Scenario:
    """读取场景, 未支持的字段或效果直接报错, 不默默降级."""
    raw = _object(json.loads(path.read_text(encoding="utf-8-sig")),
                  {"name", "description", "seed", "max_turns", "player", "enemies", "phases"}, "场景")
    player = _object(raw.get("player"),
                     {"hp", "max_hp", "max_energy", "energy", "block", "draw_per_turn", "status", "deck"},
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

    deck = []
    numeric = {"cost", "damage", "hits", "block", "vulnerable", "weak", "draw", "energy"}
    for index, entry in enumerate(_items(player.get("deck"), "player.deck")):
        where = f"player.deck[{index}]"
        card = _object(entry, numeric | {"name", "count", "target_type", "exhaust"}, where)
        values = {key: _int(value, f"{where}.{key}", 1 if key == "hits" else 0)
                  for key, value in card.items() if key in numeric}
        default_target = "AnyEnemy" if any(values.get(k, 0) for k in ("damage", "weak", "vulnerable")) else "Self"
        target = _text(card.get("target_type", default_target), f"{where}.target_type")
        if target not in {"AnyEnemy", "Self"}:
            raise ValueError(f"{where}.target_type 暂只支持 AnyEnemy 或 Self")
        if target == "Self" and any(values.get(k, 0) for k in ("damage", "weak", "vulnerable")):
            raise ValueError(f"{where} 的伤害和敌方负面效果必须指定 AnyEnemy")
        template = DeckCard(name=_text(card.get("name"), f"{where}.name"), target=target,
                            exhaust=_bool(card.get("exhaust", False), f"{where}.exhaust"), **values)
        deck.extend([template] * _int(card.get("count", 1), f"{where}.count", 1))

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
                             "VULNERABLE_POWER": "vulnerable", "WEAK_POWER": "weak"}, f"{where}.status")
            monsters.append(Monster(name=_text(enemy.get("name"), f"{where}.name"), hp=enemy_hp,
                                    max_hp=enemy_max, block=_int(enemy.get("block", 0), f"{where}.block"),
                                    script=script, entity_id=entity_id, repeat=repeat,
                                    repeat_from=repeat_from, **powers))
        phases.append(monsters)
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValueError("description 必须是字符串")
    return Scenario(name=_text(raw.get("name"), "name"), description=description,
                    player=initial, deck=deck, phases=phases,
                    seed=_int(raw.get("seed", 0), "seed"),
                    max_turns=_int(raw.get("max_turns", 60), "max_turns", 1))
