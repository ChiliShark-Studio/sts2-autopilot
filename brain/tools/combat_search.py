"""战斗工具: 对当前手牌进行单回合束搜索, 并判断药水使用时机.

使用手写评分比较本回合合法出牌, 不使用 MCTS.
由于缺少快速的完整 StS2 模拟器, 深层搜索会产生大量完整状态复制.

当前模型:

    伤害 = card.damage + strength, 易伤时乘 1.5, 虚弱时乘 0.75
    格挡 = card.block + dexterity, 脆弱时乘 0.75
    得分 = -enemy_hp - hp_weight * unblocked + 0.1 * leftover_block
           + 易伤 / 虚弱 / 力量 / 敏捷的状态价值

``hp_weight`` 随血量变化: 满血时, 自身 1 点生命约值敌方 0.4 点生命;
残血时约值敌方 2.0 点生命, 更重视格挡.

在消耗能量前检查药水, 主要用于改变保命或斩杀结果.
药水是一次性资源, 在无需药水也能获胜的战斗中使用会减少后续可用资源.

``decide_state(raw_dict)`` 只依赖状态 JSON, 便于离线基准比较和后续 C# 移植.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from ..actions import EndTurn, PlayCard, UsePotion
from ..contracts import WAIT, Decision, DecisionKind, DecisionRequest, Source
from ..effects import parse_potion
from ..model import Battle, Card, Player, Potion

# 自适应血量权重: 自身 1 点生命对应的敌方生命价值.
HP_WEIGHT_FULL = 0.4
HP_WEIGHT_LOW = 2.0
OVERBLOCK_WEIGHT = 0.1
VULNERABLE_VALUE = 3.0
WEAK_VALUE = 2.0
STRENGTH_VALUE = 2.0
DEXTERITY_VALUE = 1.5
# 使用增益药水所需的最低模拟收益, 以敌方生命值为单位.
POTION_MIN_GAIN = 15.0
# 桥接未提供升级数值时使用的固定增量.
UPGRADE_DAMAGE_BONUS = 3
UPGRADE_BLOCK_BONUS = 3
# 应急按钮造成后续无法从卡牌获得格挡时, 每回合的评分代价.
NO_BLOCK_TURN_PENALTY = 8.0

# 状态使用 id 作为键, 同时保留本地化名称别名,
# 以支持旧状态和手写测试.
_STRENGTH = {"力量", "strength", "Strength", "STRENGTH_POWER"}
_DEXTERITY = {"敏捷", "dexterity", "Dexterity", "DEXTERITY_POWER"}
_VULNERABLE = {"易伤", "vulnerable", "Vulnerable", "VULNERABLE_POWER"}
_WEAK = {"虚弱", "weak", "Weak", "WEAK_POWER"}
_FRAIL = {"脆弱", "frail", "Frail", "FRAIL_POWER"}
# 减伤状态: 第 17 层复盘暴露的两种关键效果.
_SLIPPERY = {"滑溜", "slippery", "SLIPPERY_POWER"}
_INTANGIBLE = {"无实体", "intangible", "INTANGIBLE_POWER"}
# 复制药水的 "本回合下一张牌额外打出一次" 效果对应此玩家状态.
_DUPLICATION = {"复制", "duplication", "DUPLICATION_POWER"}
# 会改变数值结算的玩家负面状态.
_SHRINK = {"缩小", "shrink", "SHRINK_POWER"}
_NO_BLOCK = {"不可格挡", "no block", "NO_BLOCK_POWER"}


def _power(powers: dict[str, int], aliases: set[str]) -> int:
    for key, value in powers.items():
        if key in aliases:
            return value
    return 0


def _upgraded(card: Card) -> Card:
    """尽量准确地生成一张手牌的升级版本.

    优先使用桥接提供的升级数值, 缺失时采用固定增量.
    后续 Mod 可以通过提供准确数值改善结果, 无需修改药水逻辑.
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
    # 滑溜: 接下来 N 次失去生命时, 每次只失去 1 点.
    slippery: int = 0
    # 无实体: 每次命中的伤害上限为 1.
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
    # 复制药水: 下一张牌打出两次.
    duplicate_next: int = 0
    # 虚弱 / 缩小影响攻击伤害, 不可格挡会阻止从卡牌获得格挡.
    weak: bool = False
    shrink: bool = False
    no_block: bool = False
    # 本回合卡牌新增的不可格挡回合数, 需要计入负面代价.
    no_block_turns: int = 0

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
            weak=_power(player.powers, _WEAK) > 0,
            shrink=_power(player.powers, _SHRINK) > 0,
            no_block=_power(player.powers, _NO_BLOCK) > 0,
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
                if self.weak:
                    dmg = int(dmg * 0.75)
                if self.shrink:
                    dmg = int(dmg * 0.70)
                # 减伤状态逐次限制命中伤害,
                # 因此多段攻击会更快消耗滑溜层数.
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
        if self.no_block:
            gain = 0  # 不可格挡: 无法从卡牌获得格挡.
        self.block += max(0, gain) * multiplier
        if card.no_block_turns:
            self.no_block_turns += card.no_block_turns

        for enemy in targets:
            enemy.vulnerable += card.vulnerable
            enemy.weak += card.weak
        self.energy += card.energy
        return True

    def total_incoming(self) -> int:
        return sum(e.incoming for e in self.enemies if e.alive)

    def firepower(self) -> int:
        """粗略估计本回合火力, 仅供日志参考, 不作为斩杀依据."""
        total = 0
        energy = self.energy
        for card in self.hand:
            if card.cost <= energy and card.can_play and card.damage > 0:
                total += (card.damage + self.strength) * card.hits
                energy -= card.cost
        return total

    def apply_potion(self, eff: dict[str, int], target: Optional[int] = None) -> None:
        """将解析后的药水效果应用到模拟状态.

        药水不消耗能量, 因此先模拟使用, 再重新规划并比较结果.
        伤害仍需经过减伤状态结算.
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
            # 强化卡牌的药水直接改变手牌,
            # 重新规划时通过普通卡牌评分评估升级收益.
            self.hand = [_upgraded(c) for c in self.hand]
        if eff.get("duplicate_next"):
            self.duplicate_next = 1
        if eff.get("draw"):
            self.energy += 0  # 抽牌尚未建模, 暂不给该效果增加收益.
        for enemy in targets:
            if eff.get("vulnerable"):
                enemy.vulnerable += eff["vulnerable"]
            if eff.get("weak"):
                enemy.weak += eff["weak"]

    @property
    def has_damage_reduction(self) -> bool:
        """存在滑溜 / 无实体时返回 True, 表示原始数值可能高估伤害."""
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
        # 应急按钮的负面效果在后续回合生效, 这里计入代价,
        # 避免搜索将 40 点格挡视为没有额外代价.
        score -= self.no_block_turns * NO_BLOCK_TURN_PENALTY
        return score


@dataclass
class _Plan:
    sequence: list[tuple[int, Optional[int]]] = field(default_factory=list)
    score: float = 0.0
    damage_taken: int = 0
    hp_left: int = 0
    won: bool = False

    @classmethod
    def from_sim(cls, sim: _Sim, sequence: list[tuple[int, Optional[int]]]) -> "_Plan":
        """记录出牌终点的评分和预计回合末结果, 不改变模拟状态."""
        damage_taken = max(0, sim.total_incoming() - sim.block)
        return cls(
            sequence=sequence,
            score=sim.value(),
            damage_taken=damage_taken,
            hp_left=sim.hp - damage_taken,
            won=sim.hp > 0 and not any(e.alive for e in sim.enemies),
        )


def _plan(sim: _Sim, beam: int = 10, depth: int = 7) -> _Plan:
    best = _Plan.from_sim(sim, [])
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
            best = _Plan.from_sim(expanded[0][1], expanded[0][2])
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

        # 同一次决策复用基础状态和搜索结果, 下一次决策仍从新状态重新计算.
        sim = _Sim.build(state.player, battle)
        plan = _plan(sim, self.beam, self.depth)

        # 药水不消耗能量, 在出牌消耗能量前检查.
        potion = self._potion(state, sim, plan)
        if potion is not None:
            return potion

        if not battle.hand:
            return Decision(action=EndTurn(), reason="手牌为空", source=Source.SCRIPT, tool=self.name)

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
        if plan.won:
            reason = f"斩杀方案: {reason}"
        return Decision(action=PlayCard(card, target), reason=reason, source=Source.SEARCH, tool=self.name)

    def _potion(self, state, sim: _Sim, base: _Plan) -> Optional[Decision]:
        """在模拟器中逐个尝试药水, 仅在收益足够时使用.

        药水不消耗能量, 但只能使用一次, 因此比较不喝药与喝药后的最佳出牌方案:

          * 当前回合将死亡时, 选择可以保命的药水.
          * 药水能使本回合斩杀时使用.
          * 危险战斗中, 增益药水带来足够收益时使用.

        基础状态和方案由本次决策传入, 避免重复构建与搜索.
        保命和斩杀均以完整出牌方案的终点为准.
        """
        player = state.player
        potions = [p for p in player.potions if p.usable]
        if not potions or base.won:
            return None

        hp_ratio = sim.hp / sim.max_hp if sim.max_hp else 1.0

        best: Optional[tuple[_Plan, Potion, Optional[str], str]] = None
        for potion in potions:
            eff = parse_potion(potion.description)
            offensive = (eff["damage"] or eff["strength"] or eff["vulnerable"]
                         or eff["weak"] or eff["upgrade_hand"] or eff["duplicate_next"])
            # 单体药水尝试所有存活敌人, 其他药水只需评估一次.
            targets: list[Optional[int]] = (
                [i for i, enemy in enumerate(sim.enemies) if enemy.alive]
                if potion.needs_enemy_target else [None]
            )
            for target_index in targets:
                trial = sim.clone()
                trial.apply_potion(eff, target_index)
                plan = _plan(trial, self.beam, self.depth)
                delta = plan.score - base.score

                if plan.hp_left <= 0:
                    continue
                if base.hp_left <= 0:
                    why = "保命"
                elif plan.won:
                    why = "斩杀"
                elif offensive and hp_ratio < 0.6 and delta >= POTION_MIN_GAIN:
                    why = "增伤"
                else:
                    continue

                if best is None or (plan.won, plan.score) > (best[0].won, best[0].score):
                    target_id = sim.enemies[target_index].entity_id if target_index is not None else None
                    best = (plan, potion, target_id, why)

        if best is None:
            return None

        plan, potion, target_id, why = best
        delta = plan.score - base.score
        return Decision(
            action=UsePotion(potion.slot, target_id),
            reason=f"{why}: {potion.name} (估值+{delta:.0f}, 血{sim.hp}/{sim.max_hp})",
            source=Source.HEURISTIC,
            tool=self.name,
        )


def decide_state(raw: dict[str, Any]) -> Decision:
    """独立入口: 输入原始状态字典, 输出决策.

    不依赖桥接, 文件或时钟. 离线基准调用此函数,
    后续 C# 移植应与其逐项对齐.
    """
    from ..model import GameState

    return CombatSearchTool().decide(
        DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw))
    )
