"""独立战斗引擎: 抽牌, 出牌, 结算, 循环执行.

真实游戏调参较慢, 每场战斗需要一定时间, 死亡还要重新开局.
本模块在本地快速模拟战斗, 便于在大量固定场景中比较参数.

复用真实游戏所用的决策函数: 每次将模拟状态渲染为桥接格式,
调用 ``combat_search.decide_state``, 再应用返回的动作.
真实游戏和竞技场之间没有隐藏的第二套策略, 行为差异需要从实现中排查.

已知的简化规则:
  * 处理基础数值效果, 感染牌及已注册的特殊意图, 不包含全部卡牌机制.
  * 敌人按固定脚本循环行动, 不复现游戏的完整行动状态机.
  * 不包含遗物, 药水或进阶难度.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

from ..model import Card


class IntentExhausted(Exception):
    """非循环行动脚本已用完, 无法继续模拟."""


@dataclass
class Intent:
    type: str = "attack"  # 意图类型: attack | defend | buff | wait | custom
    damage: int = 0
    hits: int = 1
    block: int = 0
    strength: int = 0
    handler: str = ""
    params: dict[str, Any] = field(default_factory=dict)


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
    entity_id: str = ""
    repeat: bool = True
    repeat_from: int = 0
    # 额外能力层数, 按 id 保存 (例如仪式兽的 PLOW_POWER).
    powers: dict[str, int] = field(default_factory=dict)
    # 受伤后血量降到该值(含)以下时击晕并切换阶段; None 表示没有这个机制.
    stun_below_hp: Optional[int] = None
    # 击晕后使用的新行动脚本; None 表示保持原脚本.
    next_phase_script: Optional[list[Intent]] = None
    # 被击晕的下一回合不行动.
    skip_next: bool = False

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def intent(self) -> Intent:
        if self.skip_next:
            return Intent("wait")
        if self.step < len(self.script):
            return self.script[self.step]
        if not self.repeat:
            raise IntentExhausted(f"{self.name} 的后续意图未定义")
        index = self.repeat_from + (self.step - len(self.script)) % (len(self.script) - self.repeat_from)
        return self.script[index]

    def on_damaged(self, damage: int) -> bool:
        """处理受伤后的机制, 返回是否触发了击晕."""
        if damage <= 0 or self.stun_below_hp is None or not self.alive:
            return False
        if self.hp > self.stun_below_hp:
            return False
        # 击晕: 清除击晕条件, 力量归零, 下一回合不行动, 之后改用新脚本.
        self.stun_below_hp = None
        self.strength = 0
        self.powers.pop("PLOW_POWER", None)
        self.skip_next = True
        if self.next_phase_script is not None:
            self.script = self.next_phase_script
            self.step = 0
            self.repeat_from = 0
        return True

    def attack_damage(self) -> int:
        """意图展示与实际结算共用含力量和虚弱修正的单次伤害."""
        intent = self.intent()
        if intent.type != "attack":
            return 0
        damage = max(0, intent.damage + self.strength)
        return int(damage * 0.75) if self.weak else damage


@dataclass
class DeckCard:
    """卡组中的卡牌模板, 手牌索引在抽到时分配."""

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
    playable: bool = True
    end_turn_damage: int = 0
    id: str = ""
    description: str = ""


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
    outcome: str = ""

    @property
    def hp_ratio(self) -> float:
        return self.hp_left / self.max_hp if self.max_hp else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "won": self.won, "turns": self.turns, "hp_left": self.hp_left,
            "hp_ratio": round(self.hp_ratio, 2), "damage_taken": self.damage_taken,
            "cards_played": self.cards_played, "seed": self.seed, "enemy": self.enemy,
            "outcome": self.outcome,
        }


class Fight:
    def __init__(self, deck: list[DeckCard], monsters: list[Monster], *,
                 hp: int = 70, max_hp: int = 70, max_energy: int = 3,
                 draw_per_turn: int = 5, seed: int = 0, energy: Optional[int] = None,
                 block: int = 0, strength: int = 0, dexterity: int = 0,
                 following_phases: Optional[list[list[Monster]]] = None) -> None:
        self.hp = hp
        self.max_hp = max_hp
        self.max_energy = max_energy
        self.draw_per_turn = draw_per_turn
        self.energy = max_energy if energy is None else energy
        self.block = block
        self.strength = strength
        self.dexterity = dexterity
        self.monsters = monsters
        self.following_phases = list(following_phases or [])
        self._started = False
        self.rng = random.Random(seed)
        self.seed = seed
        self.turn = 1

        self.draw_pile: list[DeckCard] = list(deck)
        self.rng.shuffle(self.draw_pile)
        self.hand: list[DeckCard] = []
        self.discard: list[DeckCard] = []
        self.cards_played = 0
        self.damage_taken = 0
        # 昏眩: 敌人施加后, 玩家下个回合只能打出 1 张牌.
        self.ringing_pending = 0
        self.ringing_active = False
        self.turn_cards_played = 0

    # -- 牌堆处理 --
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
        if self._started:
            self.block = 0
            self.energy = self.max_energy
        self._started = True
        # 昏眩只持续一个回合: 上一轮被施加的话, 本回合生效.
        self.ringing_active = self.ringing_pending > 0
        self.ringing_pending = 0
        self.turn_cards_played = 0
        self._draw(self.draw_per_turn)

    # -- 动作处理 --
    def play(self, hand_index: int, target: Optional[str] = None) -> bool:
        if hand_index < 0 or hand_index >= len(self.hand):
            return False
        card = self.hand[hand_index]
        if not card.playable or card.cost > self.energy:
            return False
        # 昏眩: 本回合已经出过一张牌, 不能再出.
        if self.ringing_active and self.turn_cards_played >= 1:
            return False
        self.energy -= card.cost
        self.hand.pop(hand_index)
        self.cards_played += 1
        self.turn_cards_played += 1

        monster = None
        if target:
            for i, candidate in enumerate(self.monsters):
                if candidate.alive and (candidate.entity_id or f"E{i}") == target:
                    monster = candidate
                    break
        if monster is None:
            alive = [m for m in self.monsters if m.alive]
            monster = alive[0] if alive else None

        if card.damage > 0 and monster is not None:
            dealt = 0
            for _ in range(max(1, card.hits)):
                dmg = max(0, card.damage + self.strength)
                if monster.vulnerable:
                    dmg = int(dmg * 1.5)
                absorbed = min(monster.block, dmg)
                monster.block -= absorbed
                hit = max(0, dmg - absorbed)
                monster.hp -= hit
                dealt += hit
            # 受伤后可能触发敌人的击晕/阶段切换 (例如仪式兽).
            monster.on_damaged(dealt)

        if card.block > 0:
            self.block += max(0, card.block + self.dexterity)

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
        # 一张牌完整结算后切换阶段, 保留玩家血量, 牌堆, 格挡, 能量和当前回合.
        if self.hp > 0 and not any(m.alive for m in self.monsters) and self.following_phases:
            self.monsters = self.following_phases.pop(0)
        return True

    def end_turn(self) -> None:
        # 先结算仍在手中的感染等回合末伤害, 再弃牌; 伤害可被当前格挡吸收.
        for card in self.hand:
            if card.end_turn_damage:
                self._take_damage(card.end_turn_damage)
                if self.hp <= 0:
                    return
        self.discard.extend(self.hand)
        self.hand = []

        for monster in self.monsters:
            if not monster.alive:
                continue
            if monster.skip_next:
                # 被击晕: 这一回合不行动, 也不推进行动脚本.
                monster.skip_next = False
                continue
            intent = monster.intent()
            damage = monster.attack_damage()
            monster.block = 0

            if intent.type == "attack":
                for _ in range(max(1, intent.hits)):
                    self._take_damage(damage)
                    if self.hp <= 0:
                        return
            elif intent.type == "defend":
                monster.block += intent.block
            elif intent.type == "custom":
                from .special_intents import CUSTOM_INTENTS

                CUSTOM_INTENTS[intent.handler]["execute"](self, monster, intent.params)

            # 所有意图都可在行动结束后增减力量, 包括攻击后强化.
            monster.strength += intent.strength
            monster.step += 1

            # 负面状态在敌人行动结束时减少持续回合.
            if monster.vulnerable:
                monster.vulnerable -= 1
            if monster.weak:
                monster.weak -= 1

    def _take_damage(self, damage: int) -> None:
        """感染和敌方攻击共用格挡结算, 仅统计实际失去的生命."""
        absorbed = min(self.block, damage)
        self.block -= absorbed
        taken = max(0, damage - absorbed)
        self.hp -= taken
        self.damage_taken += taken

    # -- 状态查询 --
    @property
    def over(self) -> bool:
        return self.hp <= 0 or self.won

    @property
    def won(self) -> bool:
        return self.hp > 0 and not any(m.alive for m in self.monsters) and not self.following_phases

    # -- 桥接格式转换 --
    def to_state(self) -> dict[str, Any]:
        """将模拟状态渲染为 ``/api/v1/singleplayer`` 返回的数据形状."""
        hand = []
        for i, card in enumerate(self.hand):
            hand.append({
                "index": i,
                "id": card.id,
                "type": "Status" if not card.playable else ("Attack" if card.damage > 0 else "Skill"),
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
                "can_play": (card.playable and card.cost <= self.energy
                             and not (self.ringing_active and self.turn_cards_played >= 1)),
                "unplayable_reason": "HasUnplayableKeyword" if not card.playable else None,
                "description": card.description,
            })

        enemies = []
        for i, monster in enumerate(self.monsters):
            if not monster.alive:
                continue
            intent = monster.intent()
            if intent.type == "wait":
                intents = [{"type": "Stun", "label": "", "title": "击晕",
                            "description": "这个敌人在其下一回合无法行动。"}]
            elif intent.type == "custom":
                from .special_intents import CUSTOM_INTENTS

                intents = CUSTOM_INTENTS[intent.handler]["preview"](self, monster, intent.params)
            else:
                intents = [{
                    "type": "Attack" if intent.type == "attack" else intent.type.title(),
                    "damage": monster.attack_damage(),
                    "hits": intent.hits,
                }]
            if intent.strength and intent.type != "buff":
                intents.append({"type": "Buff", "label": "",
                                "description": f"行动后力量变化 {intent.strength}."})
            enemies.append({
                "entity_id": monster.entity_id or f"E{i}",
                "name": monster.name,
                "hp": monster.hp,
                "max_hp": monster.max_hp,
                "block": monster.block,
                "status": self._powers(monster),
                "intents": intents,
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
                "status": ([{"id": "STRENGTH_POWER", "name": "力量", "amount": self.strength}]
                           if self.strength else []) + (
                           [{"id": "DEXTERITY_POWER", "name": "敏捷", "amount": self.dexterity}]
                           if self.dexterity else []) + (
                           [{"id": "RINGING_POWER", "name": "昏眩", "amount": 1,
                             "description": "本回合你只能打出1张牌。"}]
                           if self.ringing_active else []),
                "hand": hand,
            },
        }

    @staticmethod
    def _powers(monster: Monster) -> list[dict[str, Any]]:
        out = []
        if monster.vulnerable:
            out.append({"id": "VULNERABLE_POWER", "name": "易伤", "amount": monster.vulnerable})
        if monster.weak:
            out.append({"id": "WEAK_POWER", "name": "虚弱", "amount": monster.weak})
        if monster.strength:
            out.append({"id": "STRENGTH_POWER", "name": "力量", "amount": monster.strength})
        # 额外能力: 保留 id 和数值, 供敌人机制规则读取 (如仪式兽的横冲直撞层数).
        for power_id, amount in monster.powers.items():
            out.append({"id": power_id, "name": power_id, "amount": amount})
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
