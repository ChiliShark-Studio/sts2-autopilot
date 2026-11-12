"""精英风险评估.

地图上只显示"精英"节点, 不显示会是哪一只, 因此按本幕所有可能的精英各跑几局,
用最坏情况而不是平均值来判断要不要打.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import scenarios
from .arena import run_fight
from .engine import DeckCard

# 打完精英后至少还要留下这么多血, 才值得冒险.
SAFETY_MARGIN = 8


@dataclass
class EliteRisk:
    encounter: str
    name: str
    win_rate: float
    avg_damage: float
    max_damage: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "encounter": self.encounter, "name": self.name,
            "win_rate": round(self.win_rate, 2),
            "avg_damage": round(self.avg_damage, 1),
            "max_damage": self.max_damage,
        }


@dataclass
class EliteAssessment:
    """本幕精英的模拟结果, 按最坏情况汇总."""

    risks: list[EliteRisk] = field(default_factory=list)

    @property
    def worst_win_rate(self) -> float:
        return min((r.win_rate for r in self.risks), default=0.0)

    @property
    def worst_damage(self) -> float:
        return max((r.max_damage for r in self.risks), default=0)

    @property
    def safe(self) -> bool:
        """最坏情况能赢, 且打完还留得下继续走路的血."""
        return bool(self.risks) and self.worst_win_rate >= 0.5

    def affordable(self, hp: int, margin: int = SAFETY_MARGIN) -> bool:
        """掉血后还能留出 margin 的血才值得打."""
        return self.safe and self.worst_damage + margin <= hp

    def to_dict(self) -> dict[str, Any]:
        return {
            "worst_win_rate": round(self.worst_win_rate, 2),
            "worst_damage": self.worst_damage,
            "safe": self.safe,
            "by_elite": [r.to_dict() for r in self.risks],
        }


def assess(deck: list[DeckCard], hp: int, max_hp: int, act: int, *,
           runs: int = 3, seed: int = 0) -> EliteAssessment:
    """按本幕所有可能的精英各跑 ``runs`` 局, 返回逐精英结果."""
    result = EliteAssessment()
    for encounter in scenarios.ELITES_BY_ACT.get(act, ()):
        monsters_factory = scenarios.ENCOUNTERS.get(encounter)
        if monsters_factory is None:
            continue
        fights = [
            run_fight(deck, monsters_factory(), seed=seed + i, hp=hp, max_hp=max_hp)
            for i in range(runs)
        ]
        if not fights:
            continue
        wins = sum(1 for f in fights if f.won)
        result.risks.append(EliteRisk(
            encounter=encounter,
            name=monsters_factory()[0].name,
            win_rate=wins / len(fights),
            avg_damage=sum(f.damage_taken for f in fights) / len(fights),
            max_damage=max(f.damage_taken for f in fights),
        ))
    return result
