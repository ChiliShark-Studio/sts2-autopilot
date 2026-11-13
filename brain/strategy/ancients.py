"""先古遗物分级表: 按社区评级给出强弱, 事件里按它选.

数据在 ``knowledge/ancients.json`` (改数据不用改代码, 见 knowledge/SKILL.md)。
游戏内是中文名, 分级表主要来自英文资料: 已知中文名写在牌名里, 未收录的保留英文名,
模型认识这些英文牌名, 会自己对应到候选。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "skills" / "sts2-knowledge" / "data" / "ancients.json"


@dataclass(frozen=True)
class Ancient:
    name: str                      # 游戏内中文名 (古代事件标题)
    english: str
    tiers: tuple[tuple[str, tuple[str, ...]], ...]
    note: str = ""


def _load() -> tuple[Ancient, ...]:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    ancients = []
    for item in raw:
        tiers = tuple((tier["tier"], tuple(tier.get("relics", [])))
                      for tier in item.get("tiers", []))
        ancients.append(Ancient(item["name"], item.get("english", ""), tiers,
                                item.get("note", "")))
    return tuple(ancients)


ANCIENTS: tuple[Ancient, ...] = _load()


def guide_for(event_name: str) -> dict | None:
    """按事件中文名找先古分级; 未收录返回 None."""
    key = (event_name or "").strip()
    if not key:
        return None
    for ancient in ANCIENTS:
        if (key in ancient.name or ancient.name in key
                or key.lower() == ancient.english.lower()):
            return {
                "ancient": ancient.name,
                "english": ancient.english,
                "tiers": [{"tier": tier, "relics": list(names)}
                          for tier, names in ancient.tiers],
                "note": ancient.note,
            }
    return None
