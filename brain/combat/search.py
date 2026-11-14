"""战斗动作决策：为真实游戏和离线竞技场搜索本回合的出牌及药水方案。

本文件包含搜索所需的临时状态推演与评分，不负责接管游戏或运行完整场战斗。
共享效果由 combat.effects 结算，具体卡牌规则由 cards 中的卡牌类声明。

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
import math
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from ..actions import EndTurn, PlayCard, UsePotion
from ..contracts import WAIT, Decision, DecisionKind, DecisionRequest, Source
from ..cards import EffectKind, Event, capability, card_effects
from ..strategy.card_audit import audit
from .enemy_rules import RuleBook, attack_growth
from .effects import ActiveEffect, apply_effects, emit, power_amount, read_powers, resolve_cost, resolve_hits
from ..effects import parse_potion
from ..model import Battle, Card, Player, Potion
from ..upgrades import choose_upgrade, upgrade, upgrade_gain

# 自适应血量权重: 自身 1 点生命对应的敌方生命价值.
HP_WEIGHT_FULL = 0.4
HP_WEIGHT_LOW = 2.0
# 按战斗类型放大血量权重: 小怪战多花一张防御是划算的, 精英和 Boss 需要抢节奏换血.
# 第一幕小怪打 11 点一下不挡, 连着三四场就吃掉半管血, 到精英时已经没血可打.
# 实测 (初始牌组 70 血, 各 10 场): 小怪权重 3.5 时 nibbit 掉血 17.9 -> 15.5,
# 颚虫 10.0 -> 7.0, 史莱姆 24.3 -> 18.5, 胜率不变, 回合数 +0~1.
HP_SCALE_BY_SCREEN = {"monster": 3.5, "elite": 1.15, "boss": 1.0}
# 方案打完会死时的一次性惩罚, 远大于任何单回合收益.
DEATH_PENALTY = 1000.0
# 呼唤 (BECKON): Boss 塞进手牌的状态牌, 回合结束时留在手里每张失去 6 点生命.
# 证据: 2026-11-14 真机状态 "在你的回合结束时，如果这张牌在你的手牌中，你失去6点生命".
BECKON_HP_LOSS = 6
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
# 硬化外壳: 每回合失去的生命有上限, 直接限制击杀速度.
_HARDENED_SHELL = {"硬化外壳", "HARDENED_SHELL_POWER"}
# 呼唤: Boss 塞进手牌的状态牌, 回合结束留在手里要掉血.
_BECKON = {"呼唤", "BECKON"}
# 用生命换能量/抽牌的牌 (祭品 / 放血): 单回合模型算不出"抽到的那几张牌",
# 只能靠规则判断——血量还够、本回合又打不死敌人时, 这类牌几乎总是正收益.
_TEMPO_HP_CARDS = {"祭品", "OFFERING", "放血", "BLOODLETTING"}
# 血量比例低于该值时不再主动付生命换节奏.
TEMPO_HP_FLOOR = 0.4


def unmodeled_power(card) -> bool:
    """能力牌的效果是否完全没有被本地模型建模 (打出去等于白板).

    这类牌交给模型判断; 例如新版本新增、还没写进卡牌集的能力牌.
    """
    if str(getattr(card, "type", "")).lower() != "power":
        return False
    if any((card.damage, card.block, card.strength, card.dexterity, card.plated,
            card.cruelty, card.per_turn_strength, card.per_turn_energy,
            card.per_turn_block, card.block_per_attack)):
        return False
    return not card.effects
# 胆小: 本回合第一次被命中时先白拿一层格挡, 一回合内只触发一次.
_SKITTISH = {"胆小", "SKITTISH_POWER"}
# 复制药水的 "本回合下一张牌额外打出一次" 效果对应此玩家状态.
_DUPLICATION = {"复制", "duplication", "DUPLICATION_POWER"}
# 会改变数值结算的玩家负面状态.
_SHRINK = {"缩小", "shrink", "SHRINK_POWER"}
_NO_BLOCK = {"不可格挡", "no block", "NO_BLOCK_POWER"}
# 昏眩 (仪式兽): 本回合只能打出 1 张牌.
_RINGING = {"昏眩", "ringing", "RINGING_POWER"}
_NO_ENERGY_GAIN = {"不能获得能量", "NO_ENERGY_GAIN_POWER"}
_PYRE = {"薪火之源", "PYRE_POWER"}
# 仪式 (钙化邪教徒 +2 / 潮湿邪教徒 +5): 回合结束时固定获得力量, 攻击每回合都在涨.
_RITUAL = {"仪式", "RITUAL_POWER"}
# 魂缚锁链 (女王): 每回合最先抽到的 N 张牌只能打出 1 张.
_CHAINS = {"魂缚锁链", "CHAINS_OF_BINDING_POWER", "CHAINS_OF_BINDING"}
_SETUP_STRENGTH = {"预备打击", "SETUP_STRIKE_POWER"}
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
# 能量引擎 (每回合 +N 能量) 的额外权重: 多出的能量会让后续抽到的牌打得更顺.
ENERGY_ENGINE_UPLIFT = 1.5
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
    value += card.cruelty * 20
    value -= card.enemy_strength * 4
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

    优先使用桥接和已核对图鉴；未知牌的药水估计保留原有固定增量。
    """
    known = upgrade(card)
    if known is not card:
        return known
    if card.upgraded or card.name.endswith("+"):
        return card
    damage = card.damage
    block = card.block
    if damage:
        damage = card.damage_up if card.damage_up is not None else damage + UPGRADE_DAMAGE_BONUS
    if block:
        block = card.block_up if card.block_up is not None else block + UPGRADE_BLOCK_BONUS
    return replace(card, damage=damage, block=block, upgraded=True, damage_by_target={})


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
    # 硬化外壳 (鬼祟珊瑚群): 本回合最多失去这么多生命, 状态里给的是剩余额度.
    # -1 表示没有这个能力; 0 是"额度已经用完", 两者不能混为一谈.
    hp_loss_left: int = -1
    # 胆小 (花园幽灵鳗): 每回合第一次被命中时获得这么多格挡.
    skittish: int = 0
    skittish_used: bool = False
    # 当前力量层数.
    strength: int = 0
    # 本回合意图是"强化" (Buff).
    buff_intent: bool = False
    # 强化后攻击伤害的实际增长: 按怪物规则算 (enemy_rules), 未知怪物为 0.
    attack_growth: int = 0
    # 仪式 (RITUAL_POWER): 回合结束时固定获得的力量, 邪教徒类每回合都在涨.
    ritual: int = 0
    # 每回合固定获得的格挡 (女王 +20): 打它要先削这么多.
    steady_block: int = 0

    @property
    def scaling(self) -> bool:
        """攻击伤害是否在持续变高.

        光有力量层数不算: 闪光贾克斯果的力量涨到 18, 攻击仍然只有 3 点
        (真机 9 回合掉血 69 的实测)。只有规则确认力量会加到攻击上才算变强。
        仪式 (邪教徒) 是另一回事: 状态里写明每回合结束 +N 力量, 攻击一定跟着涨。
        """
        return self.attack_growth > 0 or self.ritual > 0

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def lose_hp(self, amount: int) -> int:
        """扣血并返回实际失去的生命; 有每回合上限时截断到剩余额度.

        证据: 2026-11-14 真机录像 logs/run-20261114-044556.jsonl 第一幕第 11 层
        鬼祟珊瑚群 (75 血) 带 HARDENED_SHELL_POWER 20, 说明为
        "每回合失去的生命值不会超过20点". 录像里每回合额度都从 20 开始,
        每打一张攻击牌按实际掉血递减 (20 -> 12 -> 3), 回合结束后重置.
        """
        if amount <= 0 or self.hp <= 0:
            return 0
        if self.hp_loss_left >= 0:
            amount = min(amount, self.hp_loss_left)
            self.hp_loss_left -= amount
        self.hp -= amount
        return amount


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
    # 残酷: 易伤敌人额外受伤的倍率 (0.25 = 再多 25%), 打出后整场生效.
    cruelty: float = 0.0
    # 阵亡自动复活 (瓶中精灵) 时回复的最大生命值百分比; 0 表示没有这类药水.
    revive_pct: int = 0
    # 本回合最多能打出几张牌 (昏眩把上限压到 1).
    play_limit: int = 99
    # 抢输出模式: 敌人在叠力量, 不再考虑纯格挡牌.
    skip_block: bool = False
    # 拘束: 这些下标被锁, 本回合只能打出其中 1 张.
    chained: frozenset[int] = frozenset()
    chained_played: bool = False
    cards_played: int = 0
    # 打出后会在后续回合持续产生的价值 (例如薪火之源每回合 +1 能量).
    future_value: float = 0.0
    # 本场已消耗的牌数 (灰烬打击按它成长).
    exhausted: int = 0
    frail: int = 0
    # 玩家身上的易伤层数: 受到的攻击伤害 x1.5. 女王第 2 回合会给 99 层.
    vulnerable: int = 0
    # 复制药水: 下一张牌打出两次.
    duplicate_next: int = 0
    # 虚弱 / 缩小影响攻击伤害, 不可格挡会阻止从卡牌获得格挡.
    weak: bool = False
    shrink: bool = False
    no_block: bool = False
    # 本回合卡牌新增的不可格挡回合数, 需要计入负面代价.
    no_block_turns: int = 0
    # 升级后的本回合收益直接进入结算；这里只保存用于未来一次再抽取的已知增量。
    upgrade_pairs: list[tuple[Card, Card]] = field(default_factory=list)
    draw_remaining: int = 0
    future_discard_size: int = 0
    cycle_known: bool = False
    max_energy: int = 3
    effect_powers: dict[str, ActiveEffect] = field(default_factory=dict)
    temporary_strength: int = 0
    growth_strength: int = 0
    no_energy_gain: bool = False
    future_energy: int = 0
    deck_attack_density: float = 0.0
    attack_hits: float = 0.0
    attack_damage: float = 0.0
    attack_cost: float = 0.0

    # 战斗类型对血量权重的放大倍数 (小怪战更珍惜生命).
    hp_value_scale: float = 1.0
    # 整副牌组: 用来估算"抽一张牌值多少" (剩余牌库 = 牌组 - 手牌).
    deck: list[Card] = field(default_factory=list)

    def expected_draw_value(self) -> float:
        """抽到一张牌的平均价值, 按还没抽到的牌库估算.

        牌库内容取"整副牌组减去当前手牌"; 牌序未知, 因此取期望而不是具体某张.
        """
        pool = list(self.deck)
        for card in self.hand:
            for index, candidate in enumerate(pool):
                if candidate.name == card.name and candidate.id == card.id:
                    pool.pop(index)
                    break
        if not pool:
            return 0.0
        return sum(_card_value(card) for card in pool) / len(pool)

    @classmethod
    def build(cls, player: Player, battle: Battle, *, hp_value_scale: float = 1.0) -> "_Sim":
        attacks = [c for c in player.deck if c.type == "attack" or c.damage or c.body_slam]
        return cls(
            hp_value_scale=hp_value_scale,
            deck=list(player.deck),
            hp=player.hp,
            max_hp=player.max_hp,
            block=player.block,
            energy=player.energy,
            hand=list(battle.hand),
            draw_remaining=battle.draw_count,
            future_discard_size=battle.discard_count + len(battle.hand) + battle.draw_count,
            cycle_known=bool(player.deck) and len(player.deck) <= (
                battle.draw_count + battle.discard_count + len(battle.hand) + battle.exhaust_count),
            max_energy=player.max_energy,
            effect_powers=read_powers(player.powers),
            temporary_strength=_power(player.powers, _SETUP_STRENGTH),
            no_energy_gain=_power(player.powers, _NO_ENERGY_GAIN) > 0,
            future_energy=_power(player.powers, _PYRE),
            deck_attack_density=len(attacks) / len(player.deck) if player.deck else 0.0,
            attack_hits=sum(c.hits for c in attacks) / len(attacks) if attacks else 0.0,
            attack_damage=sum(c.damage * c.hits for c in attacks) / len(attacks) if attacks else 0.0,
            attack_cost=sum(c.cost for c in attacks) / len(attacks) if attacks else 0.0,
            revive_pct=max((parse_potion(p.description)["death_save"]
                            for p in player.potions), default=0),
            strength=_power(player.powers, _STRENGTH),
            dexterity=_power(player.powers, _DEXTERITY),
            frail=_power(player.powers, _FRAIL),
            vulnerable=_power(player.powers, _VULNERABLE),
            duplicate_next=1 if _power(player.powers, _DUPLICATION) else 0,
            weak=_power(player.powers, _WEAK) > 0,
            # 缩小是标记型能力, 层数是 -1, 必须按"存在"判断而不是 > 0.
                    shrink=_has_power(player.powers, _SHRINK),
            no_block=_power(player.powers, _NO_BLOCK) > 0,
            # 昏眩: 本回合只能打出 1 张牌, 搜索必须按这个上限规划.
            play_limit=1 if _power(player.powers, _RINGING) > 0 else 99,
            chained=frozenset(range(min(_power(player.powers, _CHAINS),
                                              len(battle.hand)))),
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
                    hp_loss_left=(_power(e.powers, _HARDENED_SHELL)
                                  if _has_power(e.powers, _HARDENED_SHELL) else -1),
                    skittish=_power(e.powers, _SKITTISH),
                    strength=_power(e.powers, _STRENGTH),
                    buff_intent=any("buff" in intent.type.lower() for intent in e.intents),
                    ritual=_power(e.powers, _RITUAL),
                    attack_growth=attack_growth(
                        e.name, e.entity_id,
                        buff_intent=any("buff" in intent.type.lower() for intent in e.intents)),
                )
                for e in battle.alive_enemies
            ],
        )

    def clone(self) -> "_Sim":
        return copy.deepcopy(self)

    @property
    def rage(self) -> int:
        return power_amount(self, "RAGE_POWER")

    def race_needed(self) -> bool:
        """是否该抢输出而不是继续防.

        只在"敌人已经真的叠起力量"时才考虑抢输出: 单回合模型看不到它下回合
        更疼, 一直防等于慢性死亡。门槛刻意设得高——
          * 敌人已有 >= 2 点力量 (光有 Buff 意图不算, 很多敌人是攻击+Buff);
          * 自己血量 >= 六成 (残血先保命);
          * 两回合内打不死 (打得死就交给正常方案, 不叫抢输出)。
        """
        if self.hp <= 0 or (self.hp / self.max_hp if self.max_hp else 1.0) < 0.6:
            return False
        scaling = [enemy for enemy in self.enemies if enemy.alive and enemy.scaling]
        if not scaling:
            return False
        alive_hp = sum(enemy.hp for enemy in self.enemies if enemy.alive)
        return alive_hp > 2 * self.firepower()

    def legal(self) -> list[tuple[int, Optional[int]]]:
        # 昏眩等状态会限制本回合出牌数, 达到上限后只剩结束回合.
        if self.cards_played >= self.play_limit:
            return []
        out: list[tuple[int, Optional[int]]] = []
        for pos, card in enumerate(self.hand):
            if not card.can_play or resolve_cost(card.cost, card.cost_x, self.energy) > self.energy:
                continue
            # 拘束: 已经打过一张被锁的牌, 其余被锁的牌本回合不能再打.
            if self.chained_played and pos in self.chained:
                continue
            # 需要抢输出时 (敌人在叠力量), 不再把纯格挡牌算作候选.
            if self.skip_block and not (card.damage or card.body_slam or card.per_vulnerable):
                if card.block or card.plated or card.per_turn_block:
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
        available = self.energy
        cost = resolve_cost(card.cost, card.cost_x, available)
        if cost > self.energy:
            return False
        self.cards_played += 1
        self.energy -= cost
        if pos in self.chained:
            self.chained_played = True
        self.hand.pop(pos)
        # 手牌位置整体前移: 被锁的下标跟着调整.
        self.chained = frozenset(
            index if index < pos else index - 1
            for index in self.chained if index != pos)

        targets = [self.enemies[target]] if target is not None and target < len(self.enemies) else []
        if card.target_type.lower() == "allenemies":
            targets = [enemy for enemy in self.enemies if enemy.alive]
        multiplier = 2 if self.duplicate_next else 1
        self.duplicate_next = 0
        # 拆卸: 目标有易伤时攻击两次.
        hits = card.hits
        if card.hits_if_vulnerable and any(e.vulnerable for e in targets):
            hits = card.hits_if_vulnerable
        for _ in range(resolve_hits(hits, card.hits_x, available) * multiplier):
            for enemy in targets:
                if not enemy.alive:
                    continue
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
                    # 残酷: 易伤敌人再多受 cruelty 比例的伤害 (0.25 = 再多 25%).
                    dmg = int(dmg * (1.5 + self.cruelty))
                # 缓慢: 本回合每打出一张牌, 该敌人从攻击牌受到的伤害 +10%.
                # 因此攻击牌应当留到最后打, 前置的非攻击牌会抬高后续伤害.
                if enemy.slow:
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
                # 硬化外壳会截断本回合的总掉血, 超出的伤害等于白打.
                enemy.lose_hp(dmg - absorbed)
                # 胆小: 被命中之后才获得格挡, 保护的是同一回合的后续命中.
                # 真机证据 (2026-11-14 花园幽灵鳗): 御血术 15 点让 26 血降到 11 (全额),
                # 之后该敌人才出现 6 点格挡, 并挡掉后续的 3 点.
                if enemy.skittish and not enemy.skittish_used:
                    enemy.block += enemy.skittish
                    enemy.skittish_used = True

        gain = card.block + self.dexterity if card.grants_block or card.block else 0
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
        self.strength += card.temporary_strength * multiplier
        self.temporary_strength += card.temporary_strength * multiplier
        effects = card.effects or card_effects(per_turn_strength=card.per_turn_strength,
                                               block_per_attack=card.block_per_attack)
        self.growth_strength += capability(effects, Event.TURN_START, EffectKind.GAIN_STRENGTH) * multiplier
        apply_effects(self, tuple(replace(e, amount=e.amount * multiplier) for e in effects))
        self.dexterity += card.dexterity * multiplier
        if card.type == "attack" and any(e.alive for e in self.enemies):
            for _ in range(multiplier):
                emit(self, Event.ATTACK_RESOLVED)
        if card.per_turn_energy:
            self.future_energy += card.per_turn_energy * multiplier
            # 能量引擎 (薪火之源): 单回合搜索看不到后续收益, 按剩余回合折算;
            # 再乘 ENERGY_ENGINE_UPLIFT, 因为多出的能量也会让之后抽到的牌打得更顺,
            # 否则这类延迟收益的牌在束搜索里总是被当回合多打一刀挤掉.
            self.future_value += (card.per_turn_energy * ENERGY_VALUE
                                  * self.future_turns() * ENERGY_ENGINE_UPLIFT)
        if card.draw:
            # 抽牌: 用剩余牌库的平均牌价值折算, 而不是拍一个固定分.
            # 状态里有整副牌组, 扣掉手牌就是还没抽到的牌.
            self.future_value += card.draw * self.expected_draw_value()
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
        # 覆甲: 回合结束时获得格挡, 之后每回合减少 1 层.
        # 当回合的格挡已经计入 blocked, 这里补上后续回合的递减收益——
        # 只算当回合会把岩石铠甲这类牌低估成"4 点格挡的防御".
        if card.plated:
            self.plated += card.plated * multiplier
            decay = sum(max(0, card.plated - turn)
                        for turn in range(1, self.future_turns()))
            self.future_value += decay * self.hp_weight() * multiplier
        # 残酷: 整场战斗中易伤敌人额外受伤.
        # 它本身不造成伤害, 价值全在后续回合的易伤攻击上:
        # 只算当回合的话, 敌人此刻没有易伤就等于一张白板, 永远不会被打出.
        if card.cruelty:
            self.cruelty += card.cruelty * multiplier
            per_turn = max(self.firepower(), 6.0)
            self.future_value += card.cruelty * per_turn * self.future_turns() * multiplier
        # 痛殴: 随机烧掉一张攻击牌, 把它的伤害加到"这张牌"上, 下一次打出才生效.
        # 当回合仍然是 4 点两段; 加成按之后还能再抽到并打出它的概率折价.
        if card.absorb_attack:
            others = [c.damage * max(1, c.hits) for c in self.hand
                      if c is not card and c.damage > 0]
            if others:
                absorbed = sum(others) / len(others)
                deck_size = max(1, len(self.deck) or 15)
                per_draw = min(1.0, 5.0 / deck_size)
                chance = 1 - (1 - per_draw) ** max(1, self.future_turns())
                self.future_value += absorbed * card.hits * chance * multiplier
        # 与我一战！: 打中的敌人 +N 力量 (每次打出只结算一次, 不是每段命中一次).
        # 它之后还会攻击的话, 以后每回合都要多挨这些伤害.
        if card.enemy_strength and targets:
            for hit_target in targets[:1]:
                if hit_target.incoming > 0:
                    hit_target.incoming += card.enemy_strength
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
        gained = (card.energy_per_attack * sum(c.type == "attack" for c in self.hand)
                  if card.energy_per_attack else card.energy)
        if not self.no_energy_gain:
            self.energy += gained
        self.no_energy_gain = self.no_energy_gain or card.no_energy_gain
        if card.upgrade_hand:
            for _ in range(multiplier):
                positions = (list(range(len(self.hand))) if card.upgrade_all else
                             [choose_upgrade(self.hand, self.hp_weight(), ENERGY_VALUE)])
                for position in positions:
                    if position is None:
                        continue
                    before = self.hand[position]
                    improved = upgrade(before)
                    if improved is not before:
                        self.hand[position] = improved
                        self.upgrade_pairs.append((before, improved))
        return True

    def strength_future(self) -> dict[str, float]:
        """按牌组密度、基础抽牌和能量预算估算新增成长，不查看未来洗牌顺序。"""
        detail = {"per_turn_strength": self.growth_strength, "turns": 0, "hits_per_turn": 0.0,
                  "energy_per_turn": self.max_energy + self.future_energy, "estimate": 0.0}
        alive = [e for e in self.enemies if e.alive and not e.is_minion]
        if self.growth_strength <= 0 or not alive or not self.attack_hits:
            return detail
        attacks = min(5 * self.deck_attack_density,
                      max(0, self.max_energy + self.future_energy) / max(1.0, self.attack_cost))
        hits = attacks * self.attack_hits
        permanent = max(0, self.strength - self.temporary_strength)
        baseline = attacks * self.attack_damage + permanent * hits
        remaining = sum(e.hp for e in alive)
        turns = max(0, min(MAX_FUTURE_TURNS - 1, math.ceil(remaining / max(1.0, baseline)) - 1))
        estimate = 0.0
        for turn in range(1, turns + 1):
            added = self.growth_strength * turn * hits
            estimate += min(remaining, added)
            remaining -= baseline + added
            if remaining <= 0:
                turns = turn
                break
        detail.update(turns=turns, hits_per_turn=round(hits, 3), estimate=round(estimate, 3))
        return detail

    def upgrade_future(self) -> dict[str, float]:
        """有上限地估计之后一次再抽到升级牌的收益，不重复奖励本回合结算。"""
        rounds = max(0, self.future_turns() - 1)
        # 先抽完当前抽牌堆，才可能再次抽到目标；洗牌池还包含途中被抽取和弃置的牌。
        size = max(1, self.future_discard_size - self.exhausted)
        probability = min(1.0, max(0, rounds * 5 - self.draw_remaining) / size) if self.cycle_known else 0.0
        gain = sum(upgrade_gain(before, after, self.hp_weight(), ENERGY_VALUE)
                   for before, after in self.upgrade_pairs
                   if not after.exhaust and after.type != "power" and after.cost <= self.max_energy)
        if not any(e.alive and not e.is_minion for e in self.enemies):
            probability = 0.0
        return {"redraw_probability": probability, "known_gain": gain, "estimate": probability * gain}

    def hp_weight(self) -> float:
        """自身 1 点生命对应的敌方生命价值, 血量越低越值钱.

        ``hp_value_scale`` 由战斗类型决定: 小怪战多挡一刀是赚的, 精英和 Boss
        需要抢节奏, 权重保持原样.
        """
        ratio = self.hp / self.max_hp if self.max_hp else 1.0
        ratio = max(0.0, min(1.0, ratio))
        base = HP_WEIGHT_FULL + (HP_WEIGHT_LOW - HP_WEIGHT_FULL) * (1.0 - ratio)
        # Boss 战的生存压力: 每回合的伤害接近血量的几分之一时, 只看血量比例
        # 会把格挡算得太便宜, 于是拿命换输出 (女王战两回合就被打穿).
        # 只对小怪以外的场合加压 (小怪战 hp_value_scale > 1 已单独调过).
        if self.hp_value_scale <= 1.0 and self.hp > 0:
            incoming = sum(enemy.incoming for enemy in self.enemies
                           if getattr(enemy, "alive", False))
            if incoming > 0:
                turns_to_die = self.hp / incoming
                if turns_to_die <= 6:
                    base = max(base, 1.4)
        return base * self.hp_value_scale

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
            cost = resolve_cost(card.cost, card.cost_x, energy)
            if cost <= energy and card.can_play and card.damage > 0:
                total += (card.damage + self.strength) * resolve_hits(card.hits, card.hits_x, energy)
                energy -= cost
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
                enemy.lose_hp(dmg - absorbed)

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

        # 意图里的数字已经是最终伤害: 含敌人力量、我方虚弱/易伤等全部修正.
        # 核对: 女王基础 3x5 + 力量 3 -> 每段 6, 我方易伤 x1.5 -> 9x5 = 45,
        # 游戏显示的就是 45; 本地再乘一次会变成 67.
        incoming = sum(e.incoming for e in alive)
        # 覆甲在回合结束时提供格挡, 与普通格挡一样用于抵挡本回合的伤害.
        covered = min(self.block + self.plated, incoming)
        unblocked = incoming - covered

        ratio = self.hp / self.max_hp if self.max_hp else 1.0
        ratio = max(0.0, min(1.0, ratio))
        hp_weight = self.hp_weight()

        # 幻象敌人死亡后会满血复活: 打掉的血量不算战果, 否则会一直打爪牙.
        # 每回合固定格挡的敌人 (女王 +20) 打起来更亏: 等效血量按 +格挡 计.
        score = -sum(e.hp + e.steady_block for e in alive if not e.revives)
        # 呼唤: 回合结束还留在手里的每张都要掉 6 点生命, 与未挡伤害一起计入.
        beckon_loss = BECKON_HP_LOSS * sum(
            1 for card in self.hand if card.id in _BECKON or card.name in _BECKON)
        score -= (unblocked + beckon_loss) * hp_weight
        # 打完这回合会死: 单回合搜查看不到下一回合, 必须显式惩罚,
        # 否则"多挡 5 点也照样死"和"少掉 5 点血"得分几乎一样.
        if self.hp - unblocked - beckon_loss <= 0:
            score -= DEATH_PENALTY
        score += max(0, self.block - covered) * OVERBLOCK_WEIGHT
        score += sum(e.vulnerable for e in alive) * VULNERABLE_VALUE
        score += sum(e.weak for e in alive) * WEAK_VALUE
        score += (self.strength - self.temporary_strength) * STRENGTH_VALUE
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
    upgrade_future: dict[str, float] = field(default_factory=dict)
    strength_future: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_sim(cls, sim: _Sim, sequence: list[tuple[int, Optional[int]]]) -> "_Plan":
        """记录出牌终点的评分和预计回合末结果, 不改变模拟状态."""
        damage_taken = max(0, sim.total_incoming() - sim.block - sim.plated)
        hp_left = sim.hp - damage_taken
        if hp_left <= 0 and sim.revive_pct:
            # 瓶中精灵会挡住这次死亡并把生命拉回一定比例,
            # 因此"这回合会不会死"不能只看血量归零.
            hp_left = max(1, sim.max_hp * sim.revive_pct // 100)
        future = sim.upgrade_future()
        growth = sim.strength_future()
        score = sim.value() + sim.future_value + future["estimate"] + growth["estimate"]
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
            upgrade_future=future,
            strength_future=growth,
        )


def _kill_bonus(before: _Sim, after: _Sim, rules=None) -> float:
    """击杀奖励: 死掉的敌人之后几回合不会再打我们.

    只按剩余血量算的话, 打死一个 6 血小怪只值 6 分, 还不如打大怪一刀;
    这里用它的本回合伤害估算后续威胁, 让"先杀掉能杀的"有正确价值.
    例外: 某些 Boss 的爪牙死了会触发转阶段 (女王的火炬头聚合体),
    这类击杀要扣分, 否则会把"帮 Boss 解除限制"当成战果.
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
    if rules is not None:
        bonus -= rules.minion_death_penalty(before, after)
    return bonus


def _plan(sim: _Sim, beam: int = 10, depth: int = 7, rules=None) -> _Plan:
    """束搜索本回合的最佳出牌顺序.

    排序与择优都使用 ``_Plan`` 的方案评分 (包含死亡惩罚),
    不能拿 ``sim.value()`` 混着比: 那会把"多打 20 点伤害但本回合被打死"
    排到"少打一点但活下来"前面.
    ``rules`` 提供敌人机制修正 (例如打到阈值会让对方少行动一回合).
    """

    def rank(plan: _Plan) -> tuple[bool, bool, float]:
        # 可执行的斩杀与保命优先于任何未来估值，不能被巨额升级收益反超。
        return (plan.won, plan.hp_left > 0, plan.score)

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
                plan.score += _kill_bonus(node, child, rules)
                if rules is not None:
                    plan.score += rules.crossing_bonus(node, child)
                if rank(plan) > rank(best):
                    best = plan
                expanded.append((plan, child, sequence))
        if not expanded:
            break
        expanded.sort(key=lambda item: rank(item[0]), reverse=True)
        # 束搜索保留 top-beam; 延迟收益的走法 (先打引擎牌) 中间分低, 靠
        # ENERGY_ENGINE_UPLIFT / 抽牌估值把它们的终局分提前反映出来,
        # 而不是给每个首张动作预留名额——那样会挤掉更优的后续.
        frontier = [(child, seq) for _, child, seq in expanded[:beam]]
        if not any(child.legal() for child, _ in frontier):
            break
    return best


class CombatSearchTool:
    name = "combat_search"

    def __init__(self, beam: int = 10, depth: int = 7, advisor=None, rules=None,
                 tempo=None, power=None) -> None:
        self.beam = beam
        self.depth = depth
        # 每场战斗只做一次卡牌解析体检, 避免刷屏.
        self._audited = False
        # 效果无法模拟的药水 (抽牌 / 随机牌等) 只能由模型判断是否使用.
        self.advisor = advisor
        # 祭品/放血这类"付生命换节奏"的牌: 抽牌价值只能估算, 边界情况问模型.
        self.tempo = tempo
        # 完全没有建模的能力牌: 在本地模型里是白板, 打不打问模型.
        self.power = power
        # 敌人机制规则 (策略模式); 默认不假设任何机制.
        # 敌人机制规则: 未显式传入时用默认表 (仪式兽的击晕、无厌沙虫的沙坑等),
        # 规则只影响评分和"必须打某张牌"的判定, 不改变合法动作.
        self.rules = rules if rules is not None else RuleBook.with_defaults()
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

        # 战斗开始时给整副牌做一次解析体检: 描述有内容但解析没接住的牌
        # 在模型里等于白板 (残酷/覆甲/祭品抽牌/胆小/痛殴都这样漏过).
        if not self._audited:
            self._audited = True
            warnings = audit(list(battle.hand) + list(state.player.deck))
            for warning in warnings:
                print(f"[card-audit] {warning.name}: {'; '.join(warning.hints)}",
                      file=sys.stderr)

        # 同一次决策复用基础状态和搜索结果, 下一次决策仍从新状态重新计算.
        # 战斗类型决定血量权重: 小怪战多挡一刀是赚的, 精英和 Boss 抢节奏.
        scale = HP_SCALE_BY_SCREEN.get(state.screen.value, 1.0)
        sim = _Sim.build(state.player, battle, hp_value_scale=scale)
        # 女王这类 Boss 的资源结构要单独换算:
        #   * 她每回合给爪牙 +1 力量 -> 爪牙算"在变强", 优先处理;
        #   * 她每回合 +20 格挡 -> 打她先削 20, 估值时按等效血量 +20 计.
        if self.rules is not None:
            growth = self.rules.minion_growth(state)
            if growth:
                for enemy in sim.enemies:
                    if enemy.alive and enemy.is_minion:
                        enemy.attack_growth = max(enemy.attack_growth, growth)
            block = self.rules.steady_block(state)
            if block:
                for enemy in sim.enemies:
                    if enemy.alive:
                        enemy.steady_block = block
        # Boss 的胜负条件可能不在血量上: 无厌沙虫的沙坑见底会直接吞掉玩家.
        # 这类"必须先打出某张牌"的要求由敌人规则统一提供 (与仪式兽同一套).
        required_name, rule = ("", None)
        if self.rules is not None:
            required_name, rule = self.rules.required_card(state)
        if required_name:
            escape = next((card for card in battle.hand
                           if card.name.rstrip("+") == required_name and card.can_play), None)
            if escape is None:
                # 该逃但手里没有可打出的那张: 记下来, 好从日志里看出是"没抽到"
                # 还是"耗能已经涨到打不起"——这两种都说明前面该更早开始逃。
                print(f"[boss-rule] {required_name}: 手里没有可打出的, 只能按普通方案",
                      file=sys.stderr)
            else:
                sandpit = getattr(rule, "sandpit", None)
                layers = sandpit(state) if callable(sandpit) else None
                detail = f"沙坑只剩 {layers} 层" if layers is not None else "机制要求"
                return self._finish(Decision(
                    action=PlayCard(escape, None),
                    reason=f"{escape.name}: {detail}, 不处理会直接死亡, 优先活命",
                    source=Source.SEARCH, tool=self.name))

        plan = _plan(sim, self.beam, self.depth, self.rules)

        # 药水不消耗能量, 在出牌消耗能量前检查.
        potion = self._potion(state, sim, plan)
        if potion is not None:
            return potion

        advice = self._ask_advisor(state, plan, hand_is_bad=self._hand_is_bad(sim, plan))
        if advice is not None:
            return advice

        if not battle.hand:
            return self._finish(Decision(action=EndTurn(), reason="手牌为空",
                                         source=Source.SCRIPT, tool=self.name))

        # 呼唤: 本回合打不死 Boss 时, 先花 1 费把它清掉.
        # 不这么做就会每张每回合掉 6 点血: 实测 (灵魂异鱼录像, 各 30 场)
        # 满血胜率 55% -> 80%, 70 血 25% -> 37%, 60 血 5% -> 17%.
        if not plan.won:
            beckon = next((card for card in battle.hand
                           if (card.id in _BECKON or card.name in _BECKON)
                           and card.can_play
                           and resolve_cost(card.cost, card.cost_x, state.player.energy)
                           <= state.player.energy), None)
            if beckon is not None:
                return self._finish(Decision(
                    action=PlayCard(beckon, None),
                    reason="清掉呼唤: 本回合无法斩杀, 留着要掉 6 点血",
                    source=Source.SEARCH, tool=self.name))

        # 祭品 / 放血: 用生命换能量与抽牌. 抽到的牌只能按牌库平均值估算,
        # 边界情况交给模型判断; 模型说留着就不再用规则兜底.
        tempo_cards = [
            card for card in battle.hand
            if (card.id in _TEMPO_HP_CARDS or card.name in _TEMPO_HP_CARDS) and card.can_play
        ]
        if not plan.won and tempo_cards:
            verdict = "unavailable"
            if self.tempo is not None:
                advised = self.tempo.advise(state, tempo_cards)
                if advised is not None:
                    return self._finish(advised)
                verdict = self.tempo.last_verdict
            # 模型明确说"留着"时不再用规则兜底; 没有模型或回复无效才走规则.
            if verdict != "keep" and state.player.hp_ratio >= TEMPO_HP_FLOOR:
                tempo = next((card for card in tempo_cards
                              if resolve_cost(card.cost, card.cost_x, state.player.energy)
                              <= state.player.energy), None)
                if tempo is not None and plan.hp_left > 0:
                    return self._finish(Decision(
                        action=PlayCard(tempo, None),
                        reason=f"{tempo.name}: 用生命换能量与手牌, 本回合无法斩杀",
                        source=Source.SEARCH, tool=self.name))

        # 效果没建模的能力牌: 本地模型里是白板, 打不打由模型判断.
        # 没有模型时保持原行为 (不打), 不拿能量去赌一张算不出价值的牌.
        # 但本回合必死时不允许投资能力牌: 模型给过"39 伤不致命"这种说反的判断
        # (当时只有 38 血), 活命优先, 这件事由代码兜底.
        if self.power is not None and not plan.won and plan.hp_left > 0:
            powers = self.power.candidates(state)
            if powers:
                advised = self.power.advise(state, powers)
                if advised is not None:
                    # 顾问只看到牌面描述, 不知道这 1 费是从格挡里扣的:
                    # 打完之后必须还能活下来, 否则忽略建议.
                    position = next((i for i, card in enumerate(sim.hand)
                                     if card.name == advised.action.card.name), None)
                    if position is not None:
                        trial = sim.clone()
                        if trial.play(position, None):
                            follow = _plan(trial, self.beam, self.depth, self.rules)
                            if follow.hp_left <= 0:
                                self._potion_note = "能力牌会让本回合挡不住, 已忽略"
                            else:
                                return self._finish(advised)
                    else:
                        return self._finish(advised)

        # 能力牌 (尤其能量引擎) 的收益在后续回合, 单回合束搜索容易在中间层
        # 把它剪掉: 实测薪火之源 (2 费 +1 能量) 先打出去的终局分是 +5.7,
        # 而搜索选了 -39.8 的另一条。这里对每张能打的能力牌做一次"打完再规划",
        # 用完整终局分和当前方案比, 成本是每张能力牌多一次规划。
        if not plan.won:
            powers = [card for card in sim.hand
                      if card.type == "power" and card.can_play
                      and resolve_cost(card.cost, card.cost_x, sim.energy) <= sim.energy]
            best_power, best_score = None, plan.score
            for card in powers[:3]:
                position = sim.hand.index(card)
                branch = sim.clone()
                if not branch.play(position, None):
                    continue
                follow = _plan(branch, self.beam, self.depth, self.rules)
                if follow.score > best_score + 1.0:
                    best_power, best_score = card, follow.score
            if best_power is not None:
                return self._finish(Decision(
                    action=PlayCard(best_power, None),
                    reason=f"{best_power.name}: 能力牌按终局收益更优（{best_score:.0f} vs {plan.score:.0f}）",
                    source=Source.SEARCH, tool=self.name))

        if not plan.sequence:
            return self._finish(Decision(action=EndTurn(), reason="没有更优出牌",
                                         source=Source.SEARCH, tool=self.name))

        # X 费牌 (旋风斩) 用掉全部剩余能量: 先打别的牌就少一段.
        # 对每张能打的 X 费牌做一次"先打它再规划", 和当前方案比终局分.
        if not plan.won:
            for position, card in enumerate(sim.hand):
                if not card.cost_x or not card.can_play or position == plan.sequence[0][0]:
                    continue
                branch = sim.clone()
                if not branch.play(position, 0):
                    continue
                follow = _plan(branch, self.beam, self.depth, self.rules)
                if follow.score > plan.score + 1.0:
                    plan, sim = follow, branch
                    break

        # 敌人在叠力量时不能一直防: 纯格挡牌挡得住这回合, 挡不住下回合.
        # 血量健康又秒不掉时, 关掉纯格挡牌重新规划一次, 用结果和原方案比.
        first_pos, _ = plan.sequence[0]
        first = sim.hand[first_pos] if first_pos < len(sim.hand) else None
        block_only = (first is not None
                      and not (first.damage or first.body_slam or first.per_vulnerable)
                      and (first.block or first.plated or first.per_turn_block))
        if block_only and sim.race_needed():
            race_sim = sim.clone()
            race_sim.skip_block = True
            race_plan = _plan(race_sim, self.beam, self.depth, self.rules)
            if race_plan.sequence and race_plan.hp_left >= 0.2 * sim.max_hp:
                plan, sim = race_plan, race_sim

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
        details = {}
        if plan.upgrade_future["estimate"]:
            details["upgrade_future"] = plan.upgrade_future
        if plan.strength_future["estimate"]:
            details["strength_future"] = plan.strength_future
        return self._finish(Decision(action=PlayCard(card, target), reason=reason,
                                     source=Source.SEARCH, tool=self.name, extra=details))

    def _hand_is_bad(self, sim: _Sim, plan: _Plan) -> bool:
        """手牌是不是"卡死": 还有能量却花不出去, 或者该防时没有格挡牌.

        注意不能用"手牌少"当理由: 开局第一回合手牌也少, 但能量能花完,
        那时喝抽牌药水纯属浪费 (真机复盘: 女王战第一回合就喝了瓶装潜能)。
        """
        # 一张都打不出去: 这就是卡死.
        if not sim.legal():
            return True
        # 方案打完后还剩多少能量.
        spent = 0
        for pos, _ in plan.sequence:
            if pos < len(sim.hand):
                spent += resolve_cost(sim.hand[pos].cost, sim.hand[pos].cost_x, sim.energy)
        if sim.energy - spent >= 2:
            return True
        incoming = sum(enemy.incoming for enemy in sim.enemies if enemy.alive)
        has_block = any(card.block or card.plated for card in sim.hand if card.can_play)
        if incoming >= 0.3 * sim.max_hp and not has_block and sim.energy >= 2:
            return True
        return False

    def _ask_advisor(self, state, plan: _Plan, hand_is_bad: bool = False) -> Optional[Decision]:
        """方案会死且身上有无法模拟的药水时, 让模型决定是否使用.

        抽牌药水、随机无色牌这类效果取决于牌堆与随机结果, 本地无法给出可信数值;
        与其编一个收益, 不如只在"不喝就死"的局面下把选择权交给模型.
        """
        # 触发条件: 方案会死 / 血量偏低 / 手牌差且有抽牌药水.
        # 抽牌药水的价值取决于"手里有没有好牌可打", 手牌差时抽牌能救一回合;
        # 血量健康但手牌卡死时同样该问, 而不是等被磨到半血.
        if self.advisor is None:
            return None
        unknown = [p for p in state.player.potions
                   if p.usable and not potion_effects_known(parse_potion(p.description))]
        if not unknown:
            return None
        hp_ratio = state.player.hp / state.player.max_hp if state.player.max_hp else 1.0
        has_draw = any(parse_potion(p.description)["draw"] for p in unknown)
        # 攻击类药水 (随机攻击牌 / 未知伤害): 敌人已经进入"一波能收"的血线时,
        # 就该问要不要用它补那点关键伤害, 与血量无关.
        hand_damage = sum(card.damage * max(1, card.hits)
                          for card in state.battle.hand if card.can_play)
        has_offensive = any("攻击" in (p.description or "") or "伤害" in (p.description or "")
                            for p in unknown)
        near_kill = any(enemy.hp <= hand_damage + 8
                        for enemy in state.battle.alive_enemies)
        if plan.hp_left > 0 and hp_ratio >= 0.5 and not (
                (hand_is_bad and has_draw) or (has_offensive and near_kill)):
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
            # 这瓶药水的伤害能不能直接收掉一个敌人 (只差这点伤害, 与血量无关).
            # 只在"这一下能把战斗结束"时用伤害药水补刀: 多个敌人时不浪费单体药水
            # (杀一个还有另一个, 药水该留到能收掉最后一个的时候).
            alive = [enemy for enemy in sim.enemies if enemy.alive]
            kill_ready = (len(alive) == 1 and eff["damage"] > 0
                          and alive[0].hp <= eff["damage"])
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
                elif offensive and kill_ready and delta >= 0:
                    why = "补伤害"
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
