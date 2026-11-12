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
# 防御性增益 (如敏捷药水) 的收益体现为少掉血, 与火力单位不可比,
# 因此单独用生命值衡量: 危险战斗中能少掉这么多血就值得喝.
POTION_MIN_BLOCKED = 5
# 能直接结算的药水效果; 其余 (抽牌、随机牌等) 无法本地模拟.
KNOWN_EFFECTS = ("damage", "block", "heal", "strength", "dexterity",
                 "vulnerable", "weak", "energy", "duplicate_next", "upgrade_hand")


def potion_effects_known(eff: dict[str, int]) -> bool:
    """药水效果是否可以被模拟器结算."""
    return any(eff.get(name) for name in KNOWN_EFFECTS)
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
# 昏眩 (仪式兽): 本回合只能打出 1 张牌.
_RINGING = {"昏眩", "ringing", "RINGING_POWER"}
# 幻象 (寄生惧魔): 死亡时下回合满血复活, 打它等于白打.
_REVIVES = {"幻象", "illusion", "ILLUSION_POWER"}
# 爪牙: 领导者死亡时放弃战斗, 因此击杀首领等于结束战斗.
_MINION = {"爪牙", "minion", "MINION_POWER"}
# 缓慢: 每打出一张牌, 该敌人本回合从攻击牌受到的伤害 +10%.
_SLOW = {"缓慢", "slow", "SLOW_POWER"}
SLOW_STEP = 0.10
# 方案终点会死亡时的惩罚分, 让搜索优先选择活下来的打法.
LETHAL_PENALTY = 1000.0
# 每回合额外获得 1 点能量大约值多少分 (约等于一张基础攻击牌的伤害).
ENERGY_VALUE = 6.0
# 估计战斗还剩几回合: 按敌方总血量每 20 点一回合, 限制在 1~5 回合.
HP_PER_TURN = 20
MAX_FUTURE_TURNS = 5
# 击杀一个敌人除了扣掉剩余血量, 还免除了它之后几回合的输出.
# 按本回合伤害乘这个系数估算, 不按血量估, 否则"打死 6 血小怪"会被低估.
KILL_FUTURE_TURNS = 2
# 不攻击的敌人也占一个身体, 按最低这么多伤害折算.
KILL_MIN_INCOMING = 4


def _card_value(card: Card) -> float:
    """一张手牌的战斗价值, 用于估算"被烧掉"的损失.

    不能打出的一次性状态牌 (伤口等) 价值为 0, 烧掉它们等于白赚.
    """
    if not card.can_play:
        return 0.0
    value = card.damage * card.hits + card.block * 0.9
    value += card.vulnerable * 3 + card.weak * 2 + card.draw * 2 + card.energy * 3
    value += card.strength * 4 + card.dexterity * 3 + card.plated * 2
    return value


def _random_exhaust_cost(hand: list[Card], count: int) -> float:
    """随机消耗 count 张手牌的期望损失: 按剩余手牌的平均价值估."""
    if not hand or count <= 0:
        return 0.0
    average = sum(_card_value(c) for c in hand) / len(hand)
    return average * count


def _power(powers: dict[str, int], aliases: set[str]) -> int:
    for key, value in powers.items():
        if key in aliases:
            return value
    return 0


def _has_power(powers: dict[str, int], aliases: set[str]) -> bool:
    """只看能力是否存在; 数值可能为 0 (例如回合开始的缓慢), 但机制仍然生效."""
    return any(key in aliases for key in powers)


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
    # 状态能力层数 (按 id), 供敌人机制规则读取, 例如仪式兽的 PLOW_POWER.
    powers: dict[str, int] = field(default_factory=dict)
    # 幻象: 死亡后下回合满血复活, 打它的伤害不算战果.
    revives: bool = False
    # 爪牙: 领导者死亡后离场.
    is_minion: bool = False
    # 缓慢: 每打出一张牌, 本回合从攻击牌受到的伤害 +10% (层数, 每层 10%).
    slow: int = 0
    # 该敌人是否带有"缓慢"能力 (没有的话不累计).
    has_slow: bool = False
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
    # 覆甲层数: 回合结束时按层数获得格挡.
    plated: int = 0
    # 阵亡自动复活 (瓶中精灵) 时回复的最大生命值百分比; 0 表示没有这类药水.
    revive_pct: int = 0
    # 本回合最多能打出几张牌 (昏眩把上限压到 1).
    play_limit: int = 99
    cards_played: int = 0
    # 打出后会在后续回合持续产生的价值 (例如薪火之源每回合 +1 能量).
    future_value: float = 0.0
    # 本场已消耗的牌数 (灰烬打击按它成长).
    exhausted: int = 0
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
            revive_pct=max((parse_potion(p.description)["death_save"]
                            for p in player.potions), default=0),
            strength=_power(player.powers, _STRENGTH),
            dexterity=_power(player.powers, _DEXTERITY),
            frail=_power(player.powers, _FRAIL),
            duplicate_next=1 if _power(player.powers, _DUPLICATION) else 0,
            weak=_power(player.powers, _WEAK) > 0,
            # 缩小是标记型能力, 层数是 -1, 必须按"存在"判断而不是 > 0.
                    shrink=_has_power(player.powers, _SHRINK),
            no_block=_power(player.powers, _NO_BLOCK) > 0,
            # 昏眩: 本回合只能打出 1 张牌, 搜索必须按这个上限规划.
            play_limit=1 if _power(player.powers, _RINGING) > 0 else 99,
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
                    powers=dict(e.powers),
                    revives=_power(e.powers, _REVIVES) > 0,
                    is_minion=_power(e.powers, _MINION) > 0,
                    # 状态里的数值是百分比 (10 = 10%), 换算成层数.
                    slow=_power(e.powers, _SLOW) // 10,
                    has_slow=_has_power(e.powers, _SLOW),
                )
                for e in battle.alive_enemies
            ],
        )

    def clone(self) -> "_Sim":
        return copy.deepcopy(self)

    def legal(self) -> list[tuple[int, Optional[int]]]:
        # 昏眩等状态会限制本回合出牌数, 达到上限后只剩结束回合.
        if self.cards_played >= self.play_limit:
            return []
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
        self.cards_played += 1
        self.energy -= card.cost
        self.hand.pop(pos)

        targets = [self.enemies[target]] if target is not None and target < len(self.enemies) else []
        multiplier = 2 if self.duplicate_next else 1
        self.duplicate_next = 0
        # 拆卸: 目标有易伤时攻击两次.
        hits = card.hits
        if card.hits_if_vulnerable and any(e.vulnerable for e in targets):
            hits = card.hits_if_vulnerable
        for _ in range(max(1, hits) * multiplier):
            for enemy in targets:
                if not enemy.alive:
                    continue
                # 游戏自己算好的伤害: 只在"本回合第一张牌"时等价于当前状态, 直接用.
                # 后续牌会改变缓慢/易伤层数, 用本地模型推算.
                game_damage = (card.damage_by_target.get(enemy.entity_id)
                               if self.cards_played == 1 and card.damage_by_target else None)
                if game_damage is not None:
                    dmg = game_damage
                else:
                    # 条件伤害: 全身撞击用格挡, 欺凌按易伤层数, 灰烬打击按消耗数.
                    base = self.block if card.body_slam else card.damage
                    if card.per_vulnerable:
                        base += card.per_vulnerable * enemy.vulnerable
                    if card.per_exhaust:
                        base += card.per_exhaust * self.exhausted
                    dmg = base + self.strength
                    # 顺序按游戏结算: 先乘自身减伤 (虚弱/缩小), 再乘目标的易伤/缓慢.
                    # 反过来会先放大再缩水, 结果偏小 (实测重锤 34 会被算成 33).
                    if self.weak:
                        dmg = int(dmg * 0.75)
                    if self.shrink:
                        dmg = int(dmg * 0.70)
                    if enemy.vulnerable:
                        dmg = int(dmg * 1.5)
                # 缓慢: 本回合每打出一张牌, 该敌人从攻击牌受到的伤害 +10%.
                # 因此攻击牌应当留到最后打, 前置的非攻击牌会抬高后续伤害.
                # 游戏给的数字已经包含缓慢, 不能重复乘.
                if game_damage is None and enemy.slow:
                    dmg = int(dmg * (1 + SLOW_STEP * enemy.slow))
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
        # 能力牌: 成长在打出之后才生效, 只影响后续打出的牌.
        # 没有这一步, 燃烧之类的牌在模拟里等于空过一张能量.
        self.strength += card.strength * multiplier
        self.dexterity += card.dexterity * multiplier
        if card.per_turn_energy:
            # 单回合搜索看不到后续收益, 按预计剩余回合折算.
            self.future_value += card.per_turn_energy * ENERGY_VALUE * self.future_turns()
        if card.future_block_per_turn:
            # 后续回合的格挡: 只算"之后还会打几回合", 且不超过牌上写明的回合数.
            usable = max(0, min(card.future_block_turns, self.future_turns() - 1))
            self.future_value += usable * card.future_block_per_turn * self.hp_weight()
        if card.per_turn_block:
            # 每回合开始获得格挡并付出生命 (绯红披风): 按剩余回合折算净收益.
            turns = self.future_turns()
            net = max(0, card.per_turn_block - card.per_turn_hp_cost)
            self.future_value += turns * net * self.hp_weight()
        if card.exhaust:
            self.exhausted += 1
        if card.random_exhaust:
            # 随机消耗烧掉哪张不可控: 按剩余手牌的平均价值折算期望损失.
            # 先把能打的牌打完再打它, 手牌变少/变废时损失自然降低.
            self.future_value -= _random_exhaust_cost(self.hand, card.random_exhaust)
        # 覆甲: 回合结束时按层数获得格挡, 单回合模型按当回合可用格挡计.
        self.plated += card.plated * multiplier
        # 以生命为费用: 放血之外的掉血牌也要付这个代价.
        if card.hp_cost:
            self.hp = max(0, self.hp - card.hp_cost)

        for enemy in targets:
            enemy.vulnerable += card.vulnerable
            enemy.weak += card.weak
        # 缓慢: 任何一张牌打出后, 该敌人本回合受到的攻击伤害 +10% (包括非攻击牌).
        for enemy in self.enemies:
            if enemy.has_slow and enemy.alive:
                enemy.slow += 1
        self.energy += card.energy
        return True

    def hp_weight(self) -> float:
        """自身 1 点生命对应的敌方生命价值, 血量越低越值钱."""
        ratio = self.hp / self.max_hp if self.max_hp else 1.0
        ratio = max(0.0, min(1.0, ratio))
        return HP_WEIGHT_FULL + (HP_WEIGHT_LOW - HP_WEIGHT_FULL) * (1.0 - ratio)

    def future_turns(self) -> int:
        """按敌方剩余总血量粗略估计还能打几回合, 用于折算每回合收益."""
        total = sum(e.hp for e in self.enemies if e.alive)
        return max(1, min(MAX_FUTURE_TURNS, total // HP_PER_TURN))

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
        if eff.get("dexterity"):
            # 敏捷只影响之后打出的牌, 重新规划时由 play() 逐张计入格挡.
            self.dexterity += eff["dexterity"]
        if eff.get("upgrade_hand"):
            # 强化卡牌的药水直接改变手牌,
            # 重新规划时通过普通卡牌评分评估升级收益.
            self.hand = [_upgraded(c) for c in self.hand]
        if eff.get("duplicate_next"):
            self.duplicate_next = 1
        if eff.get("energy"):
            self.energy += eff["energy"]
        if eff.get("draw"):
            # 桥接的抽牌堆按稀有度排序输出, 不是抽牌顺序,
            # 因此"抽 N 张"的具体内容无法模拟; 这类药水交给模型判断.
            pass
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
        # 爪牙会在首领死亡时离场, 因此首领全灭就算赢下战斗.
        leaders = [e for e in alive if not e.is_minion]
        if not alive or not leaders:
            return 10000.0 + self.hp * 2

        incoming = sum(e.incoming for e in alive)
        # 覆甲在回合结束时提供格挡, 与普通格挡一样用于抵挡本回合的伤害.
        covered = min(self.block + self.plated, incoming)
        unblocked = incoming - covered

        ratio = self.hp / self.max_hp if self.max_hp else 1.0
        ratio = max(0.0, min(1.0, ratio))
        hp_weight = self.hp_weight()

        # 幻象敌人死亡后会满血复活: 打掉的血量不算战果, 否则会一直打爪牙.
        score = -sum(e.hp for e in alive if not e.revives)
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
        damage_taken = max(0, sim.total_incoming() - sim.block - sim.plated)
        hp_left = sim.hp - damage_taken
        if hp_left <= 0 and sim.revive_pct:
            # 瓶中精灵会挡住这次死亡并把生命拉回一定比例,
            # 因此"这回合会不会死"不能只看血量归零.
            hp_left = max(1, sim.max_hp * sim.revive_pct // 100)
        score = sim.value() + sim.future_value
        if hp_left <= 0:
            # 打输出打得再漂亮, 这回合死了也没有意义:
            # 没有这一项时, 搜索会为了多打 20 点伤害而选择不格挡.
            score -= LETHAL_PENALTY
        return cls(
            sequence=sequence,
            score=score,
            damage_taken=damage_taken,
            hp_left=hp_left,
            won=sim.hp > 0 and not any(e.alive and not e.is_minion for e in sim.enemies),
        )


def _kill_bonus(before: _Sim, after: _Sim) -> float:
    """击杀奖励: 死掉的敌人之后几回合不会再打我们.

    只按剩余血量算的话, 打死一个 6 血小怪只值 6 分, 还不如打大怪一刀;
    这里用它的本回合伤害估算后续威胁, 让"先杀掉能杀的"有正确价值.
    """
    bonus = 0.0
    for i, enemy in enumerate(after.enemies):
        if enemy.alive or i >= len(before.enemies):
            continue
        previous = before.enemies[i]
        if previous.hp <= 0:
            continue
        # 幻象敌人下回合满血复活: 这一下不是永久战果, 不给击杀奖励.
        if previous.revives:
            continue
        threat = max(previous.incoming, KILL_MIN_INCOMING) * KILL_FUTURE_TURNS
        bonus += threat * after.hp_weight()
    return bonus


def _plan(sim: _Sim, beam: int = 10, depth: int = 7, rules=None) -> _Plan:
    """束搜索本回合的最佳出牌顺序.

    排序与择优都使用 ``_Plan`` 的方案评分 (包含死亡惩罚),
    不能拿 ``sim.value()`` 混着比: 那会把"多打 20 点伤害但本回合被打死"
    排到"少打一点但活下来"前面.
    ``rules`` 提供敌人机制修正 (例如打到阈值会让对方少行动一回合).
    """

    def rank(plan: _Plan) -> tuple[int, float]:
        return (1 if plan.won else 0, plan.score)

    best = _Plan.from_sim(sim, [])
    frontier = [(sim, [])]
    for _ in range(depth):
        expanded = []
        for node, seq in frontier:
            for pos, target in node.legal():
                child = node.clone()
                if not child.play(pos, target):
                    continue
                sequence = seq + [(pos, target)]
                plan = _Plan.from_sim(child, sequence)
                plan.score += _kill_bonus(node, child)
                if rules is not None:
                    plan.score += rules.crossing_bonus(node, child)
                if rank(plan) > rank(best):
                    best = plan
                expanded.append((plan, child, sequence))
        if not expanded:
            break
        expanded.sort(key=lambda item: rank(item[0]), reverse=True)
        frontier = [(child, seq) for _, child, seq in expanded[:beam]]
        if not any(child.legal() for child, _ in frontier):
            break
    return best


class CombatSearchTool:
    name = "combat_search"

    def __init__(self, beam: int = 10, depth: int = 7, advisor=None, rules=None) -> None:
        self.beam = beam
        self.depth = depth
        # 效果无法模拟的药水 (抽牌 / 随机牌等) 只能由模型判断是否使用.
        self.advisor = advisor
        # 敌人机制规则 (策略模式); 默认不假设任何机制.
        self.rules = rules
        self._advised: dict[tuple, Optional[Decision]] = {}
        # 药水顾问选择不使用时的说明, 会附加到本次决策的日志里.
        self._potion_note = ""

    def _finish(self, decision: Decision) -> Decision:
        if not self._potion_note:
            return decision
        extra = dict(decision.extra or {})
        extra["potion_advice"] = self._potion_note
        self._potion_note = ""
        return replace(decision, extra=extra)

    def decide(self, request: DecisionRequest) -> Decision:
        state = request.state
        battle = state.battle
        if battle is None or not battle.can_act:
            return WAIT

        # 同一次决策复用基础状态和搜索结果, 下一次决策仍从新状态重新计算.
        sim = _Sim.build(state.player, battle)
        plan = _plan(sim, self.beam, self.depth, self.rules)

        # 药水不消耗能量, 在出牌消耗能量前检查.
        potion = self._potion(state, sim, plan)
        if potion is not None:
            return potion

        advice = self._ask_advisor(state, plan)
        if advice is not None:
            return advice

        if not battle.hand:
            return self._finish(Decision(action=EndTurn(), reason="手牌为空",
                                         source=Source.SCRIPT, tool=self.name))

        if not plan.sequence:
            return self._finish(Decision(action=EndTurn(), reason="没有更优出牌",
                                         source=Source.SEARCH, tool=self.name))

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
        return self._finish(Decision(action=PlayCard(card, target), reason=reason,
                                     source=Source.SEARCH, tool=self.name))

    def _ask_advisor(self, state, plan: _Plan) -> Optional[Decision]:
        """方案会死且身上有无法模拟的药水时, 让模型决定是否使用.

        抽牌药水、随机无色牌这类效果取决于牌堆与随机结果, 本地无法给出可信数值;
        与其编一个收益, 不如只在"不喝就死"的局面下把选择权交给模型.
        """
        if self.advisor is None or plan.hp_left > 0:
            return None
        unknown = [p for p in state.player.potions
                   if p.usable and not potion_effects_known(parse_potion(p.description))]
        if not unknown:
            return None

        # 同一帧可能被重复读取, 避免对相同局面反复调用模型.
        key = (
            state.battle.round, state.player.hp, state.player.energy,
            tuple(c.name for c in state.battle.hand),
            tuple(p.id for p in unknown),
        )
        if key in self._advised:
            return self._advised[key]
        advice = self.advisor.advise(state, unknown)
        if advice is None:
            # 模型选择不用时也要留痕, 否则事后看不出"为什么不喝药".
            self._potion_note = getattr(self.advisor, "last_decline", "") or "模型未给出建议"
        self._advised[key] = advice
        return advice

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
            # 敏捷与格挡都是防御性效果: 不能健康时随便喝, 但危险战斗中能少掉血就值得.
            defensive = bool(eff["dexterity"] or eff["block"])
            # 能量与抽牌的收益由重新规划衡量.
            enabling = bool(eff["energy"] or eff["draw"])
            # 单体药水尝试所有存活敌人, 其他药水只需评估一次.
            targets: list[Optional[int]] = (
                [i for i, enemy in enumerate(sim.enemies) if enemy.alive]
                if potion.needs_enemy_target else [None]
            )
            for target_index in targets:
                trial = sim.clone()
                trial.apply_potion(eff, target_index)
                plan = _plan(trial, self.beam, self.depth, self.rules)
                delta = plan.score - base.score
                # 防御性增益的价值在格挡上, 用本回合少掉的血衡量.
                blocked = base.damage_taken - plan.damage_taken

                if plan.hp_left <= 0:
                    continue
                if base.hp_left <= 0:
                    why = "保命"
                elif plan.won:
                    why = "斩杀"
                elif offensive and hp_ratio < 0.6 and delta >= POTION_MIN_GAIN:
                    why = "增伤"
                elif defensive and hp_ratio < 0.6 and blocked >= POTION_MIN_BLOCKED:
                    why = "减伤"
                elif enabling and hp_ratio < 0.6 and delta >= POTION_MIN_GAIN:
                    why = "增益"
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


def decide_state(raw: dict[str, Any], rules=None) -> Decision:
    """独立入口: 输入原始状态字典, 输出决策.

    不依赖桥接, 文件或时钟. 离线基准调用此函数,
    后续 C# 移植应与其逐项对齐.
    ``rules`` 用于传入敌人机制规则; 省略时不做任何机制假设.
    """
    from ..model import GameState

    return CombatSearchTool(rules=rules).decide(
        DecisionRequest(kind=DecisionKind.COMBAT, state=GameState.parse(raw))
    )
