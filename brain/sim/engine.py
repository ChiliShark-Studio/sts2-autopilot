"""独立战斗引擎: 抽牌, 出牌, 结算, 循环执行.

真实游戏调参较慢, 每场战斗需要一定时间, 死亡还要重新开局.
本模块在本地快速模拟战斗, 便于在大量固定场景中比较参数.

复用真实游戏所用的决策函数: 每次将模拟状态渲染为桥接格式,
调用 ``brain.combat.search.decide_state``, 再应用返回的动作.
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
from ..cards import BaseCard, Effect, Event, card_effects
from ..combat.effects import ActiveEffect, apply_effects, emit, power_amount, power_statuses, resolve_cost, resolve_hits


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
    # 滑溜: 接下来 N 次失去生命时, 每次只失去 1 点.
    slippery: int = 0
    # 缓慢: 本回合每打出一张牌, 从攻击牌受到的伤害 +10% (层数, 每层 10%).
    has_slow: bool = False
    slow: int = 0
    # 领地: 自己回合结束时获得 X 点力量 (多尼斯异鸟).
    territorial: int = 0
    # 仪式 (RITUAL_POWER): 自己回合结束时获得 X 点力量, 邪教徒类每回合都在涨.
    ritual: int = 0
    # 硬化外壳: 每回合最多失去这么多生命; 0 表示没有上限.
    # 状态 (HARDENED_SHELL_POWER) 给的是本回合剩余额度, 这里同时保存上限供新回合重置.
    hp_loss_cap: int = 0
    hp_loss_left: int = 0
    # 胆小 (花园幽灵鳗): 每回合第一次被命中时获得这么多格挡.
    skittish: int = 0
    skittish_used: bool = False
    # 战斗类型: 战斗算法按它调整血量权重 (小怪更珍惜生命).
    kind: str = "monster"
    # 爪牙: 首领死亡后离场, 首领全灭即胜利.
    is_minion: bool = False
    # 爪牙死亡后切换的行动脚本 (女王: 从"支援"切到"攻击").
    on_minion_death_script: Optional[list[Intent]] = None
    # 是否已经切换到该脚本, 避免重复切换.
    minion_phase: bool = False

    def __post_init__(self) -> None:
        # 从场景文件按上限构造时, 第一回合的额度也要跟着满上.
        if not self.hp_loss_left:
            self.hp_loss_left = self.hp_loss_cap

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def lose_hp(self, amount: int) -> int:
        """扣血并返回实际失去的生命, 超过每回合上限的部分不生效.

        证据: 真实对局 logs/run-20261114-044556.jsonl 的鬼祟珊瑚群 (75 血)
        带 HARDENED_SHELL_POWER 20, 说明为"每回合失去的生命值不会超过20点";
        每回合额度从 20 开始, 按实际掉血递减, 回合结束重置.
        """
        if amount <= 0 or self.hp <= 0:
            return 0
        if self.hp_loss_cap > 0:
            # 上限存在时 0 表示"本回合额度已用完", 不能当成没有上限.
            amount = min(amount, max(0, self.hp_loss_left))
            self.hp_loss_left -= amount
        self.hp -= amount
        return amount

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
    # 静态卡牌行为与本场实例的牌堆位置、费用等状态分开保存。
    definition: BaseCard | None = field(default=None, repr=False)
    # ---- 能力牌 (Power) ----
    # 打出时立即获得的力量 (燃烧).
    strength: int = 0
    # 每回合开始获得的力量 (恶魔形态): 打出后一直生效.
    per_turn_strength: int = 0
    block_per_attack: int = 0
    # 每次消耗牌时获得的格挡 / 抽牌 (无痛 / 黑暗之拥).
    block_per_exhaust: int = 0
    draw_per_exhaust: int = 0
    # 壁垒: 回合结束不清空格挡.
    keep_block: bool = False
    # 主宰: 每次获得格挡时对该敌人造成伤害.
    juggernaut: int = 0
    # 不可动摇: 每回合第一次从牌获得格挡时翻倍.
    double_first_block: bool = False
    # 破裂: 每次因打出牌失去生命时获得力量.
    rupture: int = 0
    # 残忍: 易伤敌人的额外受伤倍率 (0.25 = 再多 25%).
    cruelty: float = 0.0
    # 腐化: 技能牌费用变 0 且打出后消耗.
    corruption: bool = False
    # 以生命为费用 (烙印).
    hp_cost: int = 0
    # 全身撞击: 伤害等于打出时的格挡.
    scales_with_block: bool = False
    # 欺凌: 每层易伤额外伤害.
    per_vulnerable: int = 0
    # 灰烬打击: 消耗牌堆每张额外伤害.
    per_exhaust: int = 0
    cost_x: bool = False
    hits_x: bool = False


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
                 player_frail: int = 0, player_weak: int = 0, player_vulnerable: int = 0,
                 following_phases: Optional[list[list[Monster]]] = None,
                 starting_hand: Optional[list[DeckCard]] = None,
                 relic_first_block_double: bool = False,
                 screen: Optional[str] = None) -> None:
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
        # 战斗类型 (monster / elite / boss): 决定战斗算法的血量权重.
        # 未显式指定时按敌人自身的类型推断, 精英/Boss 场景不必每次手写.
        self.screen = screen or (monsters[0].kind if monsters else "monster")
        # 臂甲: 每场战斗第一次从牌获得格挡时翻倍 (只触发一次, 不按回合重置).
        self.relic_first_block_double = relic_first_block_double
        self.relic_first_block_used = False

        self.draw_pile: list[DeckCard] = list(deck)
        self.hand: list[DeckCard] = []
        # 录像场景可以给定"当前手牌", 这时先摘掉这些牌, 首回合不再额外抽牌.
        self._prefilled_hand = False
        if starting_hand:
            self.hand = list(starting_hand)
            for card in starting_hand:
                for i, candidate in enumerate(self.draw_pile):
                    if candidate == card:
                        self.draw_pile.pop(i)
                        break
            self._prefilled_hand = True
        self.rng.shuffle(self.draw_pile)
        self.discard: list[DeckCard] = []
        self.cards_played = 0
        self.damage_taken = 0
        # ---- 能力牌带来的持续状态 ----
        self.effect_powers: dict[str, ActiveEffect] = {}
        self.effect_log: list[Effect] = []
        self.block_per_exhaust = 0
        self.draw_per_exhaust = 0
        self.keep_block = False
        self.juggernaut = 0
        self.double_first_block = False
        self.first_block_used = False
        self.rupture = 0
        self.cruelty = 0.0
        self.corruption = False
        self.exhausted = 0
        # 昏眩: 敌人施加后, 玩家下个回合只能打出 1 张牌.
        self.ringing_pending = 0
        self.ringing_active = False
        self.turn_cards_played = 0
        # 玩家身上的负面状态 (女王战的 99 层脆弱/虚弱/易伤).
        self.player_weak = player_weak
        self.player_frail = player_frail
        self.player_vulnerable = player_vulnerable
        # 拘束: 每回合最先抽到的 N 张牌只能打出其中 1 张.
        self.chained_draw = 0
        self.chained_indices: set[int] = set()
        self.chained_played = False

    @property
    def per_turn_strength(self) -> int:
        return power_amount(self, "DEMON_FORM_POWER")

    @property
    def rage(self) -> int:
        return power_amount(self, "RAGE_POWER")

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

    def _apply_chains(self) -> None:
        """拘束: 本回合最先抽到的 N 张牌只能打出其中一张."""
        self.chained_indices = set()
        self.chained_played = False
        if not self.chained_draw:
            return
        for index in range(min(self.chained_draw, len(self.hand))):
            self.chained_indices.add(index)

    def start_turn(self) -> None:
        if self._started:
            if not self.keep_block:
                self.block = 0
            self.energy = self.max_energy
        self._started = True
        self.effect_log.extend(emit(self, Event.TURN_START))
        # 不可动摇: 每回合第一次获得格挡时翻倍.
        self.first_block_used = False
        # 昏眩只持续一个回合: 上一轮被施加的话, 本回合生效.
        self.ringing_active = self.ringing_pending > 0
        self.ringing_pending = 0
        self.turn_cards_played = 0
        # 缓慢在回合结束时清零.
        for monster in self.monsters:
            monster.slow = 0
            # 硬化外壳: 每回合的掉血额度在新回合重新给满.
            monster.hp_loss_left = monster.hp_loss_cap
            # 胆小: 每回合只有第一次被命中会吃格挡.
            monster.skittish_used = False
        if self._prefilled_hand:
            # 场景给定的是"当前这回合"的手牌, 不再补抽.
            self._prefilled_hand = False
        else:
            self._draw(self.draw_per_turn)
        self._apply_chains()

    # -- 动作处理 --
    def play(self, hand_index: int, target: Optional[str] = None) -> bool:
        if hand_index < 0 or hand_index >= len(self.hand):
            return False
        card = self.hand[hand_index]
        # 腐化: 技能牌 (无伤害牌) 费用变 0, 且打出后消耗.
        definition = card.definition
        corrupted = self.corruption and (definition.type == "skill" if definition else card.damage == 0)
        available = self.energy
        cost = 0 if corrupted else resolve_cost(card.cost, card.cost_x, available)
        if not card.playable or cost > self.energy:
            return False
        # 昏眩: 本回合已经出过一张牌, 不能再出.
        if self.ringing_active and self.turn_cards_played >= 1:
            return False
        # 拘束: 被锁的牌本回合只能打出一张.
        if hand_index in self.chained_indices and self.chained_played:
            return False
        self.energy -= cost
        if hand_index in self.chained_indices:
            self.chained_played = True
            self.chained_indices.discard(hand_index)
        self.hand.pop(hand_index)
        self.cards_played += 1
        self.turn_cards_played += 1

        # 以生命为费用 (烙印): 破裂会把掉血换成力量.
        if card.hp_cost:
            self.hp = max(0, self.hp - card.hp_cost)
            self.damage_taken += card.hp_cost
            if self.rupture:
                self.strength += self.rupture
        # 立即获得力量 (燃烧).
        if card.strength:
            self.strength += card.strength

        monster = None
        if target:
            for i, candidate in enumerate(self.monsters):
                if candidate.alive and (candidate.entity_id or f"E{i}") == target:
                    monster = candidate
                    break
        if monster is None:
            alive = [m for m in self.monsters if m.alive]
            monster = alive[0] if alive else None

        targets = ([enemy for enemy in self.monsters if enemy.alive] if card.target.lower() == "allenemies"
                   else [monster] if monster is not None else [])
        hits = resolve_hits(card.hits, card.hits_x, available)
        for monster in targets:
            if (card.damage > 0 or card.scales_with_block or card.per_vulnerable) and hits > 0:
                dealt = 0
                # 按牌面缩放规则算出这张牌的伤害 (全身撞击/欺凌/灰烬打击).
                base_damage = card.damage
                if card.scales_with_block:
                    base_damage = self.block
                if card.per_vulnerable and monster is not None:
                    base_damage += card.per_vulnerable * monster.vulnerable
                if card.per_exhaust:
                    base_damage += card.per_exhaust * self.exhausted
                for _ in range(hits):
                    dmg = max(0, base_damage + self.strength)
                    # 玩家虚弱: 攻击伤害 -25%.
                    if self.player_weak:
                        dmg = int(dmg * 0.75)
                    if monster.vulnerable:
                        # 残忍提高易伤额外倍率.
                        dmg = int(dmg * (1.5 + self.cruelty))
                    # 滑溜: 每次命中只掉 1 点生命, 并消耗一层.
                    if monster.slippery > 0:
                        dmg = 1
                        monster.slippery -= 1
                    # 缓慢: 本回合每打出一张牌, 该敌人受到的攻击伤害 +10%.
                    if monster.has_slow and monster.slow:
                        dmg = int(dmg * (1 + 0.1 * monster.slow))
                    absorbed = min(monster.block, dmg)
                    monster.block -= absorbed
                    # 硬化外壳会截断本回合的总掉血; 超出额度的伤害不生效.
                    hit = monster.lose_hp(max(0, dmg - absorbed))
                    dealt += hit
                    # 胆小: 被命中之后才获得格挡, 保护同一回合的后续命中.
                    if monster.skittish and not monster.skittish_used:
                        monster.block += monster.skittish
                        monster.skittish_used = True
                # 受伤后可能触发敌人的击晕/阶段切换 (例如仪式兽).
                monster.on_damaged(dealt)

        if card.block > 0:
            gain = max(0, card.block + self.dexterity)
            # 不可动摇: 每回合第一次从牌获得格挡时翻倍.
            doubled = False
            if self.double_first_block and not self.first_block_used:
                self.first_block_used = True
                doubled = True
            # 臂甲: 每场战斗只触发一次.
            if self.relic_first_block_double and not self.relic_first_block_used:
                self.relic_first_block_used = True
                doubled = True
            if doubled:
                gain *= 2
            # 玩家脆弱: 从卡牌获得的格挡 -25%.
            if self.player_frail:
                gain = int(gain * 0.75)
            self.block += gain
            # 主宰: 每次获得格挡时对敌人造成伤害.
            if self.juggernaut:
                for enemy in self.monsters:
                    if enemy.alive:
                        enemy.lose_hp(self.juggernaut)

        # 能力牌: 打出后持续生效.
        if definition is not None:
            definition.play(self)
        else:
            apply_effects(self, card_effects(per_turn_strength=card.per_turn_strength,
                                             block_per_attack=card.block_per_attack))
        self.block_per_exhaust += card.block_per_exhaust
        self.draw_per_exhaust += card.draw_per_exhaust
        self.juggernaut += card.juggernaut
        self.rupture += card.rupture
        self.cruelty += card.cruelty
        if card.keep_block:
            self.keep_block = True
        if card.double_first_block:
            self.double_first_block = True
        if card.corruption:
            self.corruption = True

        for monster in targets:
            if card.vulnerable:
                monster.vulnerable += card.vulnerable
            if card.weak:
                monster.weak += card.weak
        if card.energy:
            self.energy += card.energy
        if card.draw:
            self._draw(card.draw)

        # 消耗: 腐化的技能也计算在内; 无痛/黑暗之拥因此触发.
        if card.exhaust or corrupted:
            self.exhausted += 1
            if self.block_per_exhaust:
                self.block += self.block_per_exhaust
            if self.draw_per_exhaust:
                self._draw(self.draw_per_exhaust)
        elif definition is None or definition.type != "power":
            self.discard.append(card)
        # 一张牌完整结算后切换阶段, 保留玩家血量, 牌堆, 格挡, 能量和当前回合.
        if self.hp > 0 and not any(m.alive for m in self.monsters) and self.following_phases:
            self.monsters = self.following_phases.pop(0)
        # 缓慢: 任何一张牌 (含非攻击) 打出后层数 +1, 回合结束时清零.
        for monster in self.monsters:
            if monster.has_slow and monster.alive:
                monster.slow += 1
        kind = definition.type if definition else ("attack" if card.damage or card.scales_with_block else "skill")
        if kind == "attack" and not self.over:
            self.effect_log.extend(emit(self, Event.ATTACK_RESOLVED))
        return True

    def end_turn(self) -> None:
        self.effect_log.extend(emit(self, Event.TURN_END))
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
            if intent.type == "sleep":
                # 沉睡: 这一回合什么都不做 (旧日雕像开局).
                monster.step += 1
                continue
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
            # 领地: 自己回合结束时累积力量.
            if monster.territorial:
                monster.strength += monster.territorial
            # 仪式: 邪教徒每回合结束固定加力量.
            if monster.ritual:
                monster.strength += monster.ritual
            monster.step += 1

            # 负面状态在敌人行动结束时减少持续回合.
            if monster.vulnerable:
                monster.vulnerable -= 1
            if monster.weak:
                monster.weak -= 1

        # 爪牙死亡: 首领切换到第二阶段脚本 (女王: 从支援转为攻击).
        if not any(m.alive and m.is_minion for m in self.monsters):
            for monster in self.monsters:
                if (monster.alive and monster.on_minion_death_script is not None
                        and not monster.minion_phase):
                    monster.script = monster.on_minion_death_script
                    monster.step = 0
                    monster.minion_phase = True

    def _take_damage(self, damage: int) -> None:
        """感染和敌方攻击共用格挡结算, 仅统计实际失去的生命."""
        # 玩家身上的易伤: 受到的攻击伤害 x1.5 (女王第 2 回合给 99 层).
        if self.player_vulnerable:
            damage = int(damage * 1.5)
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
        return (self.hp > 0 and not any(m.alive and not m.is_minion for m in self.monsters)
                and not self.following_phases)

    # -- 桥接格式转换 --
    def to_state(self) -> dict[str, Any]:
        """将模拟状态渲染为 ``/api/v1/singleplayer`` 返回的数据形状."""
        hand = []
        for i, card in enumerate(self.hand):
            definition = card.definition
            hand.append({
                "index": i,
                "id": definition.id if definition is not None else card.id,
                "type": "Status" if not card.playable else (
                    definition.type.value if definition else ("Attack" if card.damage > 0 else "Skill")),
                "name": card.name,
                "is_upgraded": definition.upgraded if definition is not None else card.name.endswith("+"),
                "cost": "X" if card.cost_x else str(card.cost),
                "damage": card.damage,
                "block": card.block,
                "hits": card.hits,
                "vulnerable": card.vulnerable,
                "weak": card.weak,
                "draw": card.draw,
                "energy": card.energy,
                "target_type": card.target,
                "can_play": (card.playable and resolve_cost(card.cost, card.cost_x, self.energy) <= self.energy
                             and not (self.ringing_active and self.turn_cards_played >= 1)),
                "unplayable_reason": "HasUnplayableKeyword" if not card.playable else None,
                "description": card.description or (f"造成{card.damage}点伤害X次。" if card.hits_x else ""),
            })

        # 拘束: 本回合已经打出过一张被锁的牌, 其余被锁的牌就不能再打.
        if self.chained_played:
            for entry in hand:
                if entry["index"] in self.chained_indices:
                    entry["can_play"] = False
                    entry["unplayable_reason"] = "Chained"

        enemies = []
        for i, monster in enumerate(self.monsters):
            if not monster.alive:
                continue
            intent = monster.intent()
            if intent.type == "wait":
                intents = [{"type": "Stun", "label": "", "title": "击晕",
                            "description": "这个敌人在其下一回合无法行动。"}]
            elif intent.type == "sleep":
                intents = [{"type": "Sleep", "label": "", "title": "沉睡",
                            "description": "这个敌人在沉睡，本回合不会行动。"}]
            elif intent.type == "custom":
                from .special_intents import CUSTOM_INTENTS

                intents = CUSTOM_INTENTS[intent.handler]["preview"](self, monster, intent.params)
            else:
                # 导出"已结算"的伤害: 与游戏意图上的数字一致 (含力量 / 虚弱 / 玩家易伤),
                # 否则搜索会再乘一次易伤, 把每回合 18 点高估成 27 点.
                resolved = monster.attack_damage()
                # 与游戏一致: 导出的是最终伤害 (含我方易伤), 搜索侧不再叠加.
                if self.player_vulnerable:
                    resolved = int(resolved * 1.5)
                intents = [{
                    "type": "Attack" if intent.type == "attack" else intent.type.title(),
                    "damage": resolved,
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
            "state_type": self.screen,
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
                           # 女王战的三重 debuff: 不导出的话搜索看不到,
                           # 会以为攻击和格挡都是满值、来袭伤害也没变高.
                           [{"id": "FRAIL_POWER", "name": "脆弱", "amount": self.player_frail}]
                           if self.player_frail else []) + (
                           [{"id": "WEAK_POWER", "name": "虚弱", "amount": self.player_weak}]
                           if self.player_weak else []) + (
                           [{"id": "VULNERABLE_POWER", "name": "易伤",
                             "amount": self.player_vulnerable}]
                           if self.player_vulnerable else []) + (
                           [{"id": "RINGING_POWER", "name": "昏眩", "amount": 1,
                             "description": "本回合你只能打出1张牌。"}]
                           if self.ringing_active else []) + power_statuses(self),
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
        if monster.slippery:
            out.append({"id": "SLIPPERY_POWER", "name": "滑溜", "amount": monster.slippery})
        if monster.has_slow:
            # 状态里的数值是百分比 (每层 10%).
            out.append({"id": "SLOW_POWER", "name": "缓慢", "amount": monster.slow * 10})
        if monster.strength:
            out.append({"id": "STRENGTH_POWER", "name": "力量", "amount": monster.strength})
        if monster.hp_loss_cap > 0:
            # 导出本回合剩余额度, 与游戏状态一致 (每回合重置为上限).
            out.append({"id": "HARDENED_SHELL_POWER", "name": "硬化外壳",
                        "amount": monster.hp_loss_left})
        if monster.skittish:
            out.append({"id": "SKITTISH_POWER", "name": "胆小", "amount": monster.skittish})
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
