"""Boss 的"考试科目": 每个 Boss 考的是爆发、成长还是机制.

数据来自 Spire Codex 的 Boss 指南 (80 万局社区统计, 2026-07 快照):
  * 战斗长度是隐藏属性: 短战斗考爆发 (残杀千足虫 4.4 回合),
    长战斗考成长 (实验体 9.3 回合、墨影幻灵 9 回合);
  * 各角色的克星不同: 静默猎手怕乐加维林族母, 故障机器人怕知识恶魔,
    铁甲战士怕帝皇蟹与女王;
  * 平均掉血就是"入场费": 第二幕 Boss 约 60 血, Aeonglass 约 74 血,
    所以血量低于六成进 Boss 就是拿命赌。

结论: "所有 Boss 都要堆格挡" 是错的 —— 女王/帝皇蟹要格挡,
无厌沙虫要狂乱逃离, 灵魂异鱼要清呼唤, 实验体/墨影幻灵要成长曲线。
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "skills" / "sts2-knowledge" / "data" / "bosses.json"


def _load() -> tuple[dict, ...]:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(raw)


BOSSES: tuple[dict, ...] = _load()


def guide_for(name: str) -> dict | None:
    """按中文名或英文名找 Boss 的考试科目."""
    key = (name or "").strip()
    if not key:
        return None
    for boss in BOSSES:
        if key in (boss.get("name"), boss.get("english")):
            return boss
        if key and (key in str(boss.get("name")) or key in str(boss.get("english"))):
            return boss
    return None


def entry_fee(act: int) -> int:
    """进入该幕 Boss 战前建议保留的血量 (平均掉血即入场费)."""
    return {1: 40, 2: 60, 3: 70}.get(act, 60)


def advice_for(name: str) -> dict | None:
    """给提示词用的一段结论."""
    boss = guide_for(name)
    if boss is None:
        return None
    act = int(boss.get("act") or 1)
    return {
        "boss": boss.get("name"),
        "check": boss.get("check"),
        "death_rate": boss.get("death_rate"),
        "avg_turns": boss.get("avg_turns"),
        "note": boss.get("note"),
        "entry_hp": entry_fee(act),
    }
