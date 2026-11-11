"""Arena: play whole fights with the production combat policy, offline.

The important property is that there is no second policy: each turn is rendered
into the bridge's state format and handed to ``combat_search.decide_state``,
the exact function the live agent calls.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..tools.combat_search import decide_state
from .engine import DeckCard, Fight, FightResult, Monster

Policy = Callable[[dict[str, Any]], Any]


def run_fight(deck: list[DeckCard], monsters: list[Monster], *, seed: int = 0,
              hp: int = 70, max_hp: int = 70, max_energy: int = 3,
              max_turns: int = 60, policy: Policy = decide_state) -> FightResult:
    fight = Fight(deck, monsters, hp=hp, max_hp=max_hp, max_energy=max_energy, seed=seed)
    fight.start_turn()

    stuck = 0
    while not fight.over and fight.turn <= max_turns:
        decision = policy(fight.to_state())
        action = decision.action

        if action is not None and action.kind == "play_card":
            if fight.play(action.card.index, action.target):
                stuck = 0
                continue
            stuck += 1
        else:
            stuck = 0

        # Either the action was "end turn", or the policy asked for something
        # illegal. Both advance the round; the counter stops a policy that
        # keeps proposing the same unplayable card.
        if stuck >= 3:
            break
        fight.end_turn()
        fight.turn += 1
        if not fight.over:
            fight.start_turn()

    return FightResult(
        won=fight.won,
        turns=fight.turn,
        hp_left=max(0, fight.hp),
        max_hp=fight.max_hp,
        damage_taken=fight.damage_taken,
        cards_played=fight.cards_played,
        seed=seed,
        enemy=", ".join(m.name for m in monsters if m.alive) or ", ".join(m.name for m in monsters),
    )


@dataclass
class BatchResult:
    results: list[FightResult] = field(default_factory=list)

    @property
    def runs(self) -> int:
        return len(self.results)

    @property
    def win_rate(self) -> float:
        return sum(1 for r in self.results if r.won) / self.runs if self.runs else 0.0

    def summary(self) -> dict[str, Any]:
        if not self.results:
            return {"runs": 0}
        turns = [r.turns for r in self.results]
        taken = [r.damage_taken for r in self.results]
        left = [r.hp_left for r in self.results]
        return {
            "runs": self.runs,
            "win_rate": round(self.win_rate, 2),
            "turns_avg": round(statistics.mean(turns), 1),
            "damage_taken_avg": round(statistics.mean(taken), 1),
            "hp_left_avg": round(statistics.mean(left), 1),
            "hp_left_min": min(left),
            "hp_left_max": max(left),
        }


def run_batch(deck: list[DeckCard], monsters_factory: Callable[[], list[Monster]], *,
              runs: int = 20, hp: int = 70, max_hp: int = 70,
              policy: Optional[Policy] = None) -> BatchResult:
    policy = policy or decide_state
    out = BatchResult()
    for seed in range(runs):
        # Fresh monster instances per run: they carry mutable HP/step state.
        out.results.append(run_fight(
            list(deck), monsters_factory(), seed=seed, hp=hp, max_hp=max_hp, policy=policy))
    return out
