"""Combat tool: one-turn beam search over the real hand, plus potion timing.

This is *not* MCTS. It is a beam search over the legal plays available this
turn, scored by a hand-written evaluation. We have no fast simulator for StS2,
so a deep tree search would need hundreds of full-state clones per decision.

What it models:

    damage  = card.damage + strength, x1.5 on vulnerable, x0.75 on weak
    block   = card.block + dexterity, x0.75 on frail
    score   = -enemy_hp - hp_weight * unblocked + 0.1 * leftover_block
              + vulnerable/weak/strength/dexterity value

``hp_weight`` is adaptive: at full HP one point of our HP is worth ~0.4 enemy
HP (trade freely); at low HP it is worth ~2.0 (block first).

Potions are checked before spending energy, but only when they change the
outcome: without one we die this turn, or with one this turn kills. A potion
is a one-shot resource; drinking it on a fight we would win anyway is a loss
later.

The tool is standalone: ``decide_state(raw_dict)`` needs nothing but a state
JSON, which is what makes offline benchmarking and the eventual C# port easy.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from ..actions import EndTurn, PlayCard, UsePotion
from ..contracts import WAIT, Decision, DecisionKind, DecisionRequest, Source
from ..effects import parse_potion
from ..model import Battle, Card, Player

# Adaptive HP weight: value of 1 own HP in enemy-HP units.
HP_WEIGHT_FULL = 0.4
HP_WEIGHT_LOW = 2.0
OVERBLOCK_WEIGHT = 0.1
VULNERABLE_VALUE = 3.0
WEAK_VALUE = 2.0
STRENGTH_VALUE = 2.0
DEXTERITY_VALUE = 1.5
# Minimum simulated gain (in enemy-HP units) before a buff potion is worth it.
POTION_MIN_GAIN = 15.0
# Fallback upgrade numbers when the bridge does not expose the upgraded card.
UPGRADE_DAMAGE_BONUS = 3
UPGRADE_BLOCK_BONUS = 3

# Powers are keyed by id now; keep the localised names as aliases so older
# states (and hand-written tests) still resolve.
_STRENGTH = {"力量", "strength", "Strength", "STRENGTH_POWER"}
_DEXTERITY = {"敏捷", "dexterity", "Dexterity", "DEXTERITY_POWER"}
_VULNERABLE = {"易伤", "vulnerable", "Vulnerable", "VULNERABLE_POWER"}
_WEAK = {"虚弱", "weak", "Weak", "WEAK_POWER"}
_FRAIL = {"脆弱", "frail", "Frail", "FRAIL_POWER"}
# Damage-reduction powers: the two that made the floor-17 review pointless.
_SLIPPERY = {"滑溜", "slippery", "SLIPPERY_POWER"}
_INTANGIBLE = {"无实体", "intangible", "INTANGIBLE_POWER"}
# "本回合下一张牌额外打出一次" (复制药水) applies this power to the player.
_DUPLICATION = {"复制", "duplication", "DUPLICATION_POWER"}


def _power(powers: dict[str, int], aliases: set[str]) -> int:
    for key, value in powers.items():
        if key in aliases:
            return value
    return 0


def _upgraded(card: Card) -> Card:
    """Best-effort upgraded version of a hand card.

    Uses the bridge-provided numbers when present, otherwise a flat bonus --
    which is why a mod patch later can improve accuracy without touching the
    potion logic.
    """
    if card.upgraded:
        return card
    damage = card.damage
    block = card.block
    if damage:
        damage = card.damage_up if card.damage_up is not None else damage + UPGRADE_DAMAGE_BONUS
    if block:
        block = card.block_up if card.block_up is not None else block + UPGRADE_BLOCK_BONUS
    return replace(card, damage=damage, block=block, upgraded=True)


@dataclass
class _Enemy:
    entity_id: str
    hp: int
    max_hp: int
    block: int = 0
    incoming: int = 0
    vulnerable: int = 0
    weak: int = 0
    # 滑溜: the next N times it would lose HP, it loses 1 instead.
    slippery: int = 0
    # 无实体: every hit is capped at 1.
    intangible: bool = False

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
    # 复制药水: the next card played is played twice.
    duplicate_next: int = 0

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
            duplicate_next=1 if _power(player.powers, _DUPLICATION) else 0,
            enemies=[
                _Enemy(
                    entity_id=e.entity_id,
                    hp=e.hp,
                    max_hp=e.max_hp,
                    block=e.block,
                    incoming=e.incoming,
                    vulnerable=_power(e.powers, _VULNERABLE),
                    weak=_power(e.powers, _WEAK),
                    slippery=_power(e.powers, _SLIPPERY),
                    intangible=_power(e.powers, _INTANGIBLE) > 0,
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
        multiplier = 2 if self.duplicate_next else 1
        self.duplicate_next = 0
        for _ in range(max(1, card.hits) * multiplier):
            for enemy in targets:
                if not enemy.alive:
                    continue
                dmg = card.damage + self.strength
                if enemy.vulnerable:
                    dmg = int(dmg * 1.5)
                # Damage-reduction powers cap the hit, per hit (so multi-hit
                # attacks burn 滑溜 stacks faster -- the sim now sees that).
                if enemy.intangible:
                    dmg = min(dmg, 1)
                elif enemy.slippery > 0:
                    dmg = min(dmg, 1)
                    enemy.slippery -= 1
                absorbed = min(enemy.block, dmg)
                enemy.block -= absorbed
                enemy.hp -= max(0, dmg) - absorbed

        gain = card.block + self.dexterity
        if self.frail:
            gain = int(gain * 0.75)
        self.block += max(0, gain) * multiplier

        for enemy in targets:
            enemy.vulnerable += card.vulnerable
            enemy.weak += card.weak
        self.energy += card.energy
        return True

    def total_incoming(self) -> int:
        return sum(e.incoming for e in self.enemies if e.alive)

    def firepower(self) -> int:
        """Upper bound of damage this turn -- used by the lethal shortcut."""
        total = 0
        energy = self.energy
        for card in self.hand:
            if card.cost <= energy and card.can_play and card.damage > 0:
                total += (card.damage + self.strength) * card.hits
                energy -= card.cost
        return total

    def apply_potion(self, eff: dict[str, int], target: Optional[int] = None) -> None:
        """Apply a potion's parsed effect to this simulated state.

        Potions are free actions, so the decision layer tries one, re-plans, and
        compares. Damage still goes through the reduction powers.
        """
        targets = (
            [self.enemies[target]]
            if target is not None and target < len(self.enemies)
            else [e for e in self.enemies if e.alive]
        )

        if eff.get("damage"):
            for enemy in targets:
                if not enemy.alive:
                    continue
                dmg = eff["damage"]
                if enemy.intangible:
                    dmg = 1
                elif enemy.slippery > 0:
                    dmg = 1
                    enemy.slippery -= 1
                absorbed = min(enemy.block, dmg)
                enemy.block -= absorbed
                enemy.hp -= max(0, dmg) - absorbed

        if eff.get("block"):
            self.block += eff["block"]
        if eff.get("heal"):
            self.hp = min(self.max_hp, self.hp + eff["heal"])
        if eff.get("strength"):
            self.strength += eff["strength"]
        if eff.get("upgrade_hand"):
            # Card-enhancement potions change the hand itself; the re-plan then
            # prices the upgrade through normal card scoring.
            self.hand = [_upgraded(c) for c in self.hand]
        if eff.get("duplicate_next"):
            self.duplicate_next = 1
        if eff.get("draw"):
            self.energy += 0  # drawing is not modelled; keep the potion neutral
        for enemy in targets:
            if eff.get("vulnerable"):
                enemy.vulnerable += eff["vulnerable"]
            if eff.get("weak"):
                enemy.weak += eff["weak"]

    @property
    def has_damage_reduction(self) -> bool:
        """True when raw numbers overstate damage (滑溜 / 无实体)."""
        return any(e.slippery > 0 or e.intangible for e in self.enemies if e.alive)

    def value(self) -> float:
        alive = [e for e in self.enemies if e.alive]
        if not alive:
            return 10000.0 + self.hp * 2

        incoming = sum(e.incoming for e in alive)
        covered = min(self.block, incoming)
        unblocked = incoming - covered

        ratio = self.hp / self.max_hp if self.max_hp else 1.0
        ratio = max(0.0, min(1.0, ratio))
        hp_weight = HP_WEIGHT_FULL + (HP_WEIGHT_LOW - HP_WEIGHT_FULL) * (1.0 - ratio)

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

        # Potions are free actions: check them before spending energy.
        potion = self._potion(state)
        if potion is not None:
            return potion

        sim = _Sim.build(state.player, battle)

        # Lethal shortcut: only when the numbers are honest.
        if not sim.has_damage_reduction and sim.firepower() >= sum(e.hp for e in sim.enemies if e.alive):
            best_attack = max(
                (c for c in sim.hand if c.can_play and c.damage > 0 and c.cost <= sim.energy),
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

    def _potion(self, state) -> Optional[Decision]:
        """Try each potion in the simulator; drink only when it pays off.

        A potion is a free action but a one-shot resource, so the comparison is
        "best plan without it" versus "best plan after applying it":

          * dying this turn -> any potion that keeps us alive;
          * a potion that turns this turn lethal -> use it;
          * a buff potion in a dangerous fight, when the gain is large enough.

        This is what makes strength/dexterity/vulnerable potions usable: before,
        only raw block/heal/damage were understood.
        """
        player = state.player
        potions = [p for p in player.potions if p.usable]
        if not potions or state.battle is None:
            return None

        sim = _Sim.build(player, state.battle)
        alive = [e for e in sim.enemies if e.alive]
        if not alive:
            return None

        base = _plan(sim, self.beam, self.depth)
        if base.score >= 9000:  # value() returns 10000+ when everything dies
            return None

        net_incoming = max(0, sim.total_incoming() - sim.block)
        dying = net_incoming >= sim.hp
        hp_ratio = sim.hp / sim.max_hp if sim.max_hp else 1.0

        best: Optional[tuple[float, object, Optional[str], str]] = None
        for potion in potions:
            eff = parse_potion(potion.description)
            target_index = None
            target_id = None
            if potion.needs_enemy_target:
                target_index = max(
                    range(len(sim.enemies)),
                    key=lambda i: sim.enemies[i].hp if sim.enemies[i].alive else -1,
                )
                target_id = sim.enemies[target_index].entity_id

            trial = sim.clone()
            trial.apply_potion(eff, target_index)
            plan = _plan(trial, self.beam, self.depth)
            delta = plan.score - base.score

            if dying:
                after = max(0, trial.total_incoming() - trial.block)
                if trial.hp > after and (best is None or delta > best[0]):
                    best = (delta, potion, target_id, "保命")
                continue

            if plan.score >= 9000:
                if best is None or delta > best[0]:
                    best = (delta, potion, target_id, "斩杀")
                continue

            offensive = (eff["damage"] or eff["strength"] or eff["vulnerable"]
                         or eff["weak"] or eff["upgrade_hand"] or eff["duplicate_next"])
            if offensive and hp_ratio < 0.6 and delta >= POTION_MIN_GAIN:
                if best is None or delta > best[0]:
                    best = (delta, potion, target_id, "增伤")

        if best is None:
            return None

        delta, potion, target_id, why = best
        return Decision(
            action=UsePotion(potion.slot, target_id),
            reason=f"{why}：{potion.name}（估值+{delta:.0f}，血{sim.hp}/{sim.max_hp}）",
            source=Source.HEURISTIC,
            tool=self.name,
        )


def decide_state(raw: dict[str, Any]) -> Decision:
    """Standalone entry point: a raw state dict in, a decision out.

    No bridge, no files, no clocks. This is the function a C# port must match
    bit-for-bit, and the one the offline benchmark calls.
    """
    from ..model import GameState

    return CombatSearchTool().decide(
        DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw))
    )
