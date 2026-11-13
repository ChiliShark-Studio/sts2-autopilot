"""卡牌与机制的组合知识: 只在相关牌出现在牌组或候选里时才注入提示词.

数据在 ``knowledge/combos.json`` (改数据不用改代码, 见 knowledge/SKILL.md)。
角色提示词讲通用原则, 具体的牌组合放这里, 避免提示词越来越长还互相冲突。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "skills" / "sts2-knowledge" / "data" / "combos.json"


@dataclass(frozen=True)
class Note:
    """一条组合知识."""

    topic: str
    cards: tuple[str, ...]
    text: str


def _load() -> tuple[Note, ...]:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(Note(item["topic"], tuple(item.get("cards", [])), item.get("note", ""))
                 for item in raw)


NOTES: tuple[Note, ...] = _load()


def relevant_notes(names) -> list[dict[str, str]]:
    """按当前可用的牌名挑出相关条目; ``names`` 可以是牌组与候选牌的并集."""
    available = {name.rstrip("+") for name in names if name}
    picked = [note for note in NOTES if any(card in available for card in note.cards)]
    return [{"topic": note.topic, "note": note.text} for note in picked]
