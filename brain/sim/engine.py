"""A standalone combat engine: draw, play, resolve, repeat.

Why this exists: tuning the combat policy against the live game is slow (one
fight per minute, and a death costs a whole run).  This module plays a fight
locally in milliseconds, so parameters can be compared over hundreds of runs.

It deliberately reuses the *same* decision function as the live agent: every
turn the engine renders itself into the bridge's state format, calls
``combat_search.decide_state``, and applies whatever comes back.  If the live
bot and the arena disagree, that is a bug in one of them -- there is no second
policy hidden here.

Known simplifications (documented, not accidental):
  * card text beyond damage/block/draw/energy/vulnerable/weak is ignored;
  * enemy scripts are fixed cycles, not the game's move machine;
  * no relics, no potions, no ascension.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

from ..model import Card


@dataclass
class Intent:
    type: str = "attack"  # attack | defend | buff
    damage: int = 0
    hits: int = 1
    block: int = 0
    strength: int = 0


@dataclass
class Monster:
    name: str
    hp: int
    max_hp: int
    script: list[Intent] = field(default_factory=lambda: [Intent("attack", 10)])
    block: int = 0
    vulnerable: int = 0
    weak: int = 0
    strength: int = 0
    step: int = 0

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def intent(self) -> Intent:
        return self.script[self.step % len(self.script)]


@dataclass
class DeckCard:
    """A card template in the deck (no hand index; that is assigned on draw)."""

    name: str
    cost: int = 1
    damage: int = 0
    hits: int = 1
    block: int = 0
    vulnerable: int = 0
    weak: int = 0
    draw: int = 0
    energy: int = 0
    target: str = "AnyEnemy"
    exhaust: bool = False


@dataclass
class FightResult:
    won: bool
    turns: int
    hp_left: int
    max_hp: int
    damage_taken: int
    cards_played: int
    seed: int
    enemy: str

    @property
    def hp_ratio(self) -> float:
        return self.hp_left / self.max_hp if self.max_hp else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "won": self.won, "turns": self.turns, "hp_left": self.hp_left,
            "hp_ratio": round(self.hp_ratio, 2), "damage_taken": self.damage_taken,
            "cards_played": self.cards_played, "seed": self.seed, "enemy": self.enemy,
        }


class Fight:
    def __init__(self, deck: list[DeckCard], monsters: list[Monster], *,
                 hp: int = 70, max_hp: int = 70, max_energy: int = 3,
                 draw_per_turn: int = 5, seed: int = 0) -> None:
        self.hp = hp
        self.max_hp = max_hp
        self.max_energy = max_energy
        self.draw_per_turn = draw_per_turn
        self.energy = max_energy
        self.block = 0
        self.strength = 0
        self.dexterity = 0
        self.monsters = monsters
        self.rng = random.Random(seed)
        self.seed = seed
        self.turn = 1

        self.draw_pile: list[DeckCard] = list(deck)
        self.rng.shuffle(self.draw_pile)
        self.hand: list[DeckCard] = []
        self.discard: list[DeckCard] = []
        self.cards_played = 0
        self.damage_taken = 0

    # -- pile handling ----------------------------------------------------- #
    def _draw(self, count: int) -> None:
        for _ in range(count):
            if len(self.hand) >= 10:
                break
            if not self.draw_pile:
                if not self.discard:
                    break
                self.draw_pile, self.discard = self.discard, []
                self.rng.shuffle(self.draw_pile)
            self.hand.append(self.draw_pile.pop())

    def start_turn(self) -> None:
        self.block = 0
        self.energy = self.max_energy
        self._draw(self.draw_per_turn)

    # -- actions ----------------------------------------------------------- #
    def play(self, hand_index: int, target: Optional[str] = None) -> bool:
        if hand_index < 0 or hand_index >= len(self.hand):
            return False
        card = self.hand[hand_index]
        if card.cost > self.energy:
            return False
        self.energy -= card.cost
        self.hand.pop(hand_index)
        self.cards_played += 1

        monster = None
        if target:
            for i, candidate in enumerate(self.monsters):
                if candidate.alive and f"E{i}" == target:
                    monster = candidate
                    break
        if monster is None:
            alive = [m for m in self.monsters if m.alive]
            monster = alive[0] if alive else None

        if card.damage > 0 and monster is not None:
            for _ in range(max(1, card.hits)):
                dmg = card.damage + self.strength
                if monster.vulnerable:
                    dmg = int(dmg * 1.5)
                if monster.weak:
                    dmg = int(dmg * 0.75)
                absorbed = min(monster.block, dmg)
                monster.block -= absorbed
                monster.hp -= max(0, dmg - absorbed)

        if card.block > 0:
            self.block += card.block + self.dexterity

        if card.vulnerable and monster is not None:
            monster.vulnerable += card.vulnerable
        if card.weak and monster is not None:
            monster.weak += card.weak
        if card.energy:
            self.energy += card.energy
        if card.draw:
            self._draw(card.draw)

        if not card.exhaust:
            self.discard.append(card)
        return True

    def end_turn(self) -> None:
        self.discard.extend(self.hand)
        self.hand = []

        for monster in self.monsters:
            if not monster.alive:
                continue
            intent = monster.intent()
            monster.step += 1
            monster.block = 0

            if intent.type == "attack" and intent.damage > 0:
                for _ in range(max(1, intent.hits)):
                    dmg = intent.damage + monster.strength
                    if monster.weak:
                        dmg = int(dmg * 0.75)
                    absorbed = min(self.block, dmg)
                    self.block -= absorbed
                    taken = max(0, dmg - absorbed)
                    self.hp -= taken
                    self.damage_taken += taken
            elif intent.type == "defend":
                monster.block += intent.block
            elif intent.type == "buff":
                monster.strength += intent.strength

            # Debuffs tick down at end of the enemy's action.
            if monster.vulnerable:
                monster.vulnerable -= 1
            if monster.weak:
                monster.weak -= 1

    # -- queries ----------------------------------------------------------- #
    @property
    def over(self) -> bool:
        return self.hp <= 0 or not any(m.alive for m in self.monsters)

    @property
    def won(self) -> bool:
        return self.hp > 0 and not any(m.alive for m in self.monsters)

    # -- bridge-format rendering ------------------------------------------- #
    def to_state(self) -> dict[str, Any]:
        """Render into the exact shape ``/api/v1/singleplayer`` returns."""
        hand = []
        for i, card in enumerate(self.hand):
            hand.append({
                "index": i,
                "name": card.name,
                "cost": str(card.cost),
                "damage": card.damage,
                "block": card.block,
                "hits": card.hits,
                "vulnerable": card.vulnerable,
                "weak": card.weak,
                "draw": card.draw,
                "energy": card.energy,
                "target_type": card.target,
                "can_play": card.cost <= self.energy,
                "description": "",
            })

        enemies = []
        for i, monster in enumerate(self.monsters):
            if not monster.alive:
                continue
            intent = monster.intent()
            enemies.append({
                "entity_id": f"E{i}",
                "name": monster.name,
                "hp": monster.hp,
                "max_hp": monster.max_hp,
                "block": monster.block,
                "status": self._powers(monster),
                "intents": [{
                    "type": "Attack" if intent.type == "attack" else intent.type.title(),
                    "damage": intent.damage,
                    "hits": intent.hits,
                }],
            })

        return {
            "state_type": "monster",
            "battle": {
                "round": self.turn,
                "turn": "player",
                "ready_for_action": True,
                "enemies": enemies,
            },
            "player": {
                "hp": self.hp,
                "max_hp": self.max_hp,
                "block": self.block,
                "energy": self.energy,
                "status": [{"name": "力量", "amount": self.strength}] if self.strength else [],
                "hand": hand,
            },
        }

    @staticmethod
    def _powers(monster: Monster) -> list[dict[str, Any]]:
        out = []
        if monster.vulnerable:
            out.append({"name": "易伤", "amount": monster.vulnerable})
        if monster.weak:
            out.append({"name": "虚弱", "amount": monster.weak})
        if monster.strength:
            out.append({"name": "力量", "amount": monster.strength})
        return out


def deck_from_cards(cards: list[Card]) -> list[DeckCard]:
    return [
        DeckCard(
            name=c.name or c.id, cost=c.cost, damage=c.damage, hits=c.hits,
            block=c.block, vulnerable=c.vulnerable, weak=c.weak, draw=c.draw,
            energy=c.energy, target=c.target_type or "AnyEnemy",
        )
        for c in cards
    ]
