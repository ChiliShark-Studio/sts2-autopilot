"""事件选项数据: 按社区统计的"提升度"给出建议.

数据在 ``knowledge/events.json`` (改数据不用改代码, 见 knowledge/SKILL.md)。
提升度 = 该选项的胜率 - 同一批玩家从该楼层起的预期胜率, 单位是百分点。
注意: 提升度是相关不是因果; 受牌组结构影响的选择 (例如自助指南) 要按效果判断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "skills" / "sts2-knowledge" / "data" / "events.json"


@dataclass(frozen=True)
class EventGuide:
    name: str
    # (选项标题关键词, 提升度, 说明)
    options: tuple[tuple[str, float, str], ...]
    note: str = ""


def _load() -> tuple[EventGuide, ...]:
    """读取数据文件; 缺失或损坏时返回空表 (调用方按"未收录"处理)."""
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    guides = []
    for item in raw:
        options = tuple((option["choice"], float(option["lift"]), option.get("note", ""))
                        for option in item.get("options", []))
        guides.append(EventGuide(item["name"], options, item.get("note", "")))
    return tuple(guides)


EVENTS: tuple[EventGuide, ...] = _load()


def guide_for(name: str) -> dict | None:
    """按事件中文名找选项数据; 支持包含匹配 (游戏标题可能带后缀)."""
    key = (name or "").strip()
    if not key:
        return None
    for guide in EVENTS:
        if key in guide.name or guide.name in key:
            ranked = sorted(guide.options, key=lambda item: item[1], reverse=True)
            return {
                "event": guide.name,
                "options": [{"choice": option, "lift": lift, "note": note}
                            for option, lift, note in ranked],
                "note": guide.note,
                "source": "Spire Codex 事件统计 2026-11",
            }
    return None
