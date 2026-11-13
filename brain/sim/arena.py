"""竞技场: 离线使用真实游戏所用的战斗策略打完整场战斗.

这里没有第二套策略. 每次将状态渲染为桥接格式,
交给真实 Agent 同样调用的 ``brain.combat.search.decide_state`` 决策.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..combat.search import decide_state
from .engine import DeckCard, Fight, FightResult, IntentExhausted, Monster
from .scenario_file import Scenario

Policy = Callable[[dict[str, Any]], Any]


def run_fight(deck: list[DeckCard], monsters: list[Monster], *, seed: int = 0,
              hp: int = 70, max_hp: int = 70, max_energy: int = 3,
              max_turns: int = 60, policy: Policy = decide_state) -> FightResult:
    fight = Fight(deck, monsters, hp=hp, max_hp=max_hp, max_energy=max_energy, seed=seed)
    return _play_fight(fight, max_turns=max_turns, policy=policy)


def _play_fight(fight: Fight, *, max_turns: int, policy: Policy) -> FightResult:
    """内置遭遇与文件场景共用同一战斗循环, 不支持的动作报告为未完成."""
    fight.start_turn()
    outcome = "incomplete:turn_limit"
    decisions = 0
    while not fight.over and fight.turn <= max_turns:
        # 零费抽牌等循环可能不结束回合, 需要独立的决策次数上限.
        if decisions >= max_turns * 100:
            outcome = "incomplete:action_limit"
            break
        decisions += 1
        try:
            decision = policy(fight.to_state())
        except IntentExhausted:
            outcome = "incomplete:intents_exhausted"
            break
        action = decision.action

        if action is not None and action.kind == "play_card":
            if fight.play(action.card.index, action.target):
                continue
            outcome = "incomplete:illegal_action"
            break
        if action is None or action.kind != "end_turn":
            outcome = "incomplete:unsupported_action"
            break
        fight.end_turn()
        if not fight.over:
            if fight.turn == max_turns:
                break
            fight.turn += 1
            fight.start_turn()

    if fight.won:
        outcome = "win"
    elif fight.hp <= 0:
        outcome = "loss"
    return FightResult(
        won=fight.won,
        turns=fight.turn,
        hp_left=max(0, fight.hp),
        max_hp=fight.max_hp,
        damage_taken=fight.damage_taken,
        cards_played=fight.cards_played,
        seed=fight.seed,
        enemy=", ".join(m.name for m in fight.monsters if m.alive) or ", ".join(m.name for m in fight.monsters),
        outcome=outcome,
    )


@dataclass
class BatchResult:
    results: list[FightResult] = field(default_factory=list)

    @property
    def runs(self) -> int:
        return len(self.results)

    @property
    def win_rate(self) -> Optional[float]:
        completed = [r for r in self.results if r.outcome in ("win", "loss")]
        return sum(r.won for r in completed) / len(completed) if completed else None

    def summary(self) -> dict[str, Any]:
        if not self.results:
            return {"runs": 0}
        turns = [r.turns for r in self.results]
        taken = [r.damage_taken for r in self.results]
        left = [r.hp_left for r in self.results]
        return {
            "runs": self.runs,
            "wins": sum(r.outcome == "win" for r in self.results),
            "losses": sum(r.outcome == "loss" for r in self.results),
            "incomplete": sum(r.outcome.startswith("incomplete:") for r in self.results),
            "win_rate": round(self.win_rate, 2) if self.win_rate is not None else None,
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
        # 每次运行创建新的敌人实例, 避免复用已改变的血量和行动状态.
        out.results.append(run_fight(
            list(deck), monsters_factory(), seed=seed, hp=hp, max_hp=max_hp, policy=policy))
    return out


def run_scenario(scenario: Scenario, *, runs: int = 1,
                 policy: Policy = decide_state) -> BatchResult:
    """以文件中的种子为起点连续运行, 每场重新初始化所有状态."""
    if runs < 1:
        raise ValueError("运行场数必须大于等于 1")
    return BatchResult([
        _play_fight(scenario.create_fight(scenario.seed + offset),
                    max_turns=scenario.max_turns, policy=policy)
        for offset in range(runs)
    ])
