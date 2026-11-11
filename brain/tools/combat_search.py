"""Combat tool: one-turn beam search over the real hand.

This is *not* MCTS.  It is a beam search over the legal plays available this
turn, scored by a hand-written evaluation.  That is a deliberate choice: we
have no fast simulator for StS2 (the reference project uses a C++ one), so a
deep tree search would need hundreds of full-game clones per decision.

What it models (and this is where accuracy comes from):

    damage  = card.damage + strength, x1.5 on vulnerable, x0.75 on weak
    block   = card.block + dexterity, x0.75 on frail
    score   = -enemy_hp - hp_weight * unblocked + 0.1 * leftover_block
              + vulnerable/weak value

``hp_weight`` is *adaptive*: at full HP one point of our HP is worth ~0.4 enemy
HP (trade freely), at low HP it is worth ~2.0 (block first).  A constant weight
is what made the first version face-tank until it died.

The tool is standalone: ``decide_state(raw_dict)`` needs nothing but a state
JSON, which is what makes offline benchmarking and the eventual C# port easy.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

from ..actions import EndTurn, PlayCard
from ..contracts import WAIT, Decision, DecisionKind, DecisionRequest, Source
from ..model import Battle, Card, Player

# Adaptive HP weight: value of 1 own HP in enemy-HP units.
HP_WEIGHT_FULL = 0.4
HP_WEIGHT_LOW = 2.0
OVERBLOCK_WEIGHT = 0.1
VULNERABLE_VALUE = 3.0
WEAK_VALUE = 2.0
STRENGTH_VALUE = 2.0
DEXTERITY_VALUE = 1.5

# The bridge localises power names, so match every spelling we have seen.
_STRENGTH = {"力量", "strength", "Strength"}
_DEXTERITY = {"敏捷", "dexterity", "Dexterity"}
_VULNERABLE = {"易伤", "vulnerable", "Vulnerable"}
_WEAK = {"虚弱", "weak", "Weak"}
_FRAIL = {"脆弱", "frail", "Frail"}


def _power(powers: dict[str, int], aliases: set[str]) -> int:
    for key, value in powers.items():
        if key in aliases:
            return value
    return 0


@dataclass
class _Enemy:
    entity_id: str
    hp: int
    max_hp: int
    block: int = 0
    incoming: int = 0
    vulnerable: int = 0
    weak: int = 0

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class _Sim:
    hp: int
    max_hp: int
    block: int
    energy: int
    hand: list[Card]
    enemies: list[_Enemy]
    strength: int = 0
    dexterity: int = 0
    frail: int = 0

    @classmethod
    def build(cls, player: Player, battle: Battle) -> "_Sim":
        return cls(
            hp=player.hp,
            max_hp=player.max_hp,
            block=player.block,
            energy=player.energy,
            hand=list(battle.hand),
            strength=_power(player.powers, _STRENGTH),
            dexterity=_power(player.powers, _DEXTERITY),
            frail=_power(player.powers, _FRAIL),
            enemies=[
                _Enemy(
                    entity_id=e.entity_id,
                    hp=e.hp,
                    max_hp=e.max_hp,
                    block=e.block,
                    incoming=e.incoming,
                    vulnerable=_power(e.powers, _VULNERABLE),
                    weak=_power(e.powers, _WEAK),
                )
                for e in battle.alive_enemies
            ],
        )

    def clone(self) -> "_Sim":
        return copy.deepcopy(self)

    def legal(self) -> list[tuple[int, Optional[int]]]:
        out: list[tuple[int, Optional[int]]] = []
        for pos, card in enumerate(self.hand):
            if not card.can_play or card.cost > self.energy:
                continue
            if card.needs_target:
                for i, enemy in enumerate(self.enemies):
                    if enemy.alive:
                        out.append((pos, i))
            else:
                out.append((pos, None))
        return out

    def play(self, pos: int, target: Optional[int]) -> bool:
        card = self.hand[pos]
        if card.cost > self.energy:
            return False
        self.energy -= card.cost
        self.hand.pop(pos)

        targets = [self.enemies[target]] if target is not None and target < len(self.enemies) else []
        for _ in range(max(1, card.hits)):
            for enemy in targets:
                if not enemy.alive:
                    continue
                dmg = card.damage + self.strength
                if enemy.vulnerable:
                    dmg = int(dmg * 1.5)
                absorbed = min(enemy.block, dmg)
                enemy.block -= absorbed
                enemy.hp -= max(0, dmg) - absorbed

        gain = card.block + self.dexterity
        if self.frail:
            gain = int(gain * 0.75)
        self.block += max(0, gain)

        for enemy in targets:
            enemy.vulnerable += card.vulnerable
            enemy.weak += card.weak
        self.energy += card.energy
        return True

    def firepower(self) -> int:
        """Upper bound of damage this turn -- used only by the lethal shortcut."""
        total = 0
        energy = self.energy
        for card in self.hand:
            if card.cost <= energy and card.can_play and card.damage > 0:
                total += (card.damage + self.strength) * card.hits
                energy -= card.cost
        return total

    def value(self) -> float:
        alive = [e for e in self.enemies if e.alive]
        if not alive:
            return 10000.0 + self.hp * 2

        incoming = sum(e.incoming for e in alive)
        covered = min(self.block, incoming)
        unblocked = incoming - covered

        ratio = self.hp / self.max_hp if self.max_hp else 1.0
        hp_weight = HP_WEIGHT_FULL + (HP_WEIGHT_LOW - HP_WEIGHT_FULL) * (1.0 - max(0.0, min(1.0, ratio)))

        score = -sum(e.hp for e in alive)
        score -= unblocked * hp_weight
        score += max(0, self.block - covered) * OVERBLOCK_WEIGHT
        score += sum(e.vulnerable for e in alive) * VULNERABLE_VALUE
        score += sum(e.weak for e in alive) * WEAK_VALUE
        score += self.strength * STRENGTH_VALUE
        score += self.dexterity * DEXTERITY_VALUE
        return score


@dataclass
class _Plan:
    sequence: list[tuple[int, Optional[int]]] = field(default_factory=list)
    score: float = 0.0


def _plan(sim: _Sim, beam: int = 10, depth: int = 7) -> _Plan:
    best = _Plan([], sim.value())
    frontier = [(sim, [])]
    for _ in range(depth):
        expanded = []
        for node, seq in frontier:
            for pos, target in node.legal():
                child = node.clone()
                if not child.play(pos, target):
                    continue
                expanded.append((child.value(), child, seq + [(pos, target)]))
        if not expanded:
            break
        expanded.sort(key=lambda item: item[0], reverse=True)
        frontier = [(node, seq) for _, node, seq in expanded[:beam]]
        if expanded[0][0] > best.score:
            best = _Plan(expanded[0][2], expanded[0][0])
    return best


class CombatSearchTool:
    name = "combat_search"

    def __init__(self, beam: int = 10, depth: int = 7) -> None:
        self.beam = beam
        self.depth = depth

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        battle = state.battle
        if battle is None or not battle.can_act:
            return WAIT
        if not battle.hand:
            return Decision(action=EndTurn(), reason="手牌为空", source=Source.SCRIPT, tool=self.name)

        sim = _Sim.build(state.player, battle)

        # Lethal shortcut: if this turn can kill everything, no need to search
        # far -- just take the biggest attack (the search confirms it anyway).
        if sim.firepower() >= sum(e.hp for e in sim.enemies if e.alive):
            best_attack = max(
                (c for c in sim.hand if c.damage > 0 and c.cost <= sim.energy),
                key=lambda c: c.damage * c.hits,
                default=None,
            )
            if best_attack is not None:
                target = max((e for e in sim.enemies if e.alive), key=lambda e: e.hp, default=None)
                return Decision(
                    action=PlayCard(best_attack, target.entity_id if target else None),
                    reason=f"斩杀线内（火力 {sim.firepower()} ≥ 敌方总血），打最大伤害",
                    source=Source.SEARCH,
                    tool=self.name,
                )

        plan = _plan(sim, self.beam, self.depth)
        if not plan.sequence:
            return Decision(action=EndTurn(), reason="没有更优出牌", source=Source.SEARCH, tool=self.name)

        pos, target_index = plan.sequence[0]
        card = sim.hand[pos]
        target = None
        if target_index is not None:
            alive = [e for e in sim.enemies if e.alive]
            target = alive[target_index].entity_id
        reason = (
            f"{card.name} 伤害{card.damage}+{sim.strength} 格挡{card.block}+{sim.dexterity}"
            f" | 估值{plan.score:.0f} | 火力{sim.firepower()}"
        )
        return Decision(action=PlayCard(card, target), reason=reason, source=Source.SEARCH, tool=self.name)


def decide_state(raw: dict[str, Any]) -> Decision:
    """Standalone entry point: a raw state dict in, a decision out.

    No bridge, no files, no clocks.  This is the function a C# port must match
    bit-for-bit, and the one the offline benchmark calls.
    """
    from ..model import GameState

    return CombatSearchTool().decide(
        DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw))
    )
