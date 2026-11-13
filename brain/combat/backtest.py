"""真机录像回测: 用当前模拟器重放录下来的决策, 对比模拟血量与游戏实际血量.

数据来源是 ``tools/extract_combat.py`` 抽出的 ``data/combat/*.jsonl``.
每条记录含完整状态与实际决策, 因此同一份录像可以反复用来验证模拟器的伤害、
状态与敌人机制是否和游戏一致; 算法改动后跑一遍就能看出有没有回退.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import GameState
from .search import _Sim


@dataclass
class Mismatch:
    step: Any
    card: str
    simulated: dict[str, int]
    recorded: dict[str, int]


@dataclass
class Report:
    path: Path
    matched: int = 0
    skipped: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def compared(self) -> int:
        return self.matched + len(self.mismatches)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def hp_map(raw: dict[str, Any]) -> dict[str, int]:
    battle = raw.get("battle") or {}
    return {e.get("entity_id"): e.get("hp") for e in (battle.get("enemies") or []) if isinstance(e, dict)}


def compare(path: Path) -> Report:
    """只比较同一回合内的连续两步: 跨回合会掺入敌方行动, 不归模拟器解释."""
    rows = load(path)
    report = Report(path=path)
    for before, after in zip(rows, rows[1:]):
        action = (before.get("decision") or {}).get("action") or {}
        if action.get("action") != "play_card":
            report.skipped += 1
            continue
        if (after.get("turn"), after.get("fight")) != (before.get("turn"), before.get("fight")):
            report.skipped += 1
            continue
        state = GameState.parse(before["state"])
        sim = _Sim.build(state.player, state.battle)
        target = action.get("target")
        index = next((i for i, e in enumerate(sim.enemies) if e.entity_id == target), None)
        sim.play(int(action["card_index"]), index)
        simulated = {e.entity_id: e.hp for e in sim.enemies}
        recorded = hp_map(after["state"])
        if simulated == recorded:
            report.matched += 1
            continue
        hand = [c.get("name") for c in ((before["state"].get("player") or {}).get("hand") or [])]
        position = int(action["card_index"])
        report.mismatches.append(Mismatch(
            step=before.get("step"),
            card=hand[position] if 0 <= position < len(hand) else "?",
            simulated=simulated, recorded=recorded,
        ))
    return report
