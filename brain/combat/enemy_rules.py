"""敌人机制与搜索修正：为战斗搜索提供已核实的机制知识和评分奖励。

本文件不结算模拟器中的敌人行动，不直接产生动作；默认对未知敌人不做任何假设。

战斗搜索只依赖状态里可见的信息 (意图、状态、血量), 但有些机制需要"下一步会发生什么":
例如仪式兽在血量跌破阈值后会击晕自己一个回合, 这个信息在触发前看不到.
这类知识放在这里, 每条规则都必须写明证据来源和适用版本, 失效时能被测试发现.

设计:
  * ``EnemyRule`` 是策略接口, 每个敌人一个实现;
  * ``RuleBook`` 按敌人 id / 名称分派, 未登记敌人返回空规则, 行为与之前完全一致;
  * 规则只影响评分与预测, 不直接产生动作, 这样规则写错也不会让机器人做非法操作.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 击中"击晕阈值"相当于白赚一个回合, 用固定分数量化, 与敌方生命值单位一致.
STUN_TURN_VALUE = 15.0
# 击晕同时清空敌方力量时, 每点力量额外值多少分.
# 一点力量约等于每回合少挨一点伤害, 按两个回合的收益保守估算.
STRENGTH_RESET_VALUE = 2.0


class EnemyRule:
    """单个敌人的机制规则. 未覆盖的机制保持默认(无修正)."""

    ids: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    def stun_threshold_hp(self, enemy) -> Optional[int]:
        """血量降到该值(含)以下且受到伤害时, 敌人会被击晕; None 表示无此机制.

        优先从状态里的能力层数读取, 不要写死比例:
        游戏本身会在状态里给出触发条件, 写死会随版本和进阶难度漂移.
        """
        return None

    def resets_strength_on_stun(self) -> bool:
        """击晕时是否会清空敌方力量 (力量越高, 这一下的价值越大)."""
        return False

    def attack_growth_after_buff(self, enemy) -> int:
        """敌人这回合"强化"之后, 下回合攻击伤害实际增长多少.

        默认 0: 不知道它的 Buff 是什么就不假设伤害会涨, 避免凭空制造威胁。
        """
        return 0

    def required_card(self, state) -> str:
        """这一回合必须先打出的牌名; 空字符串表示没有强制要求.

        用于"胜负条件不在血量上"的机制: 无厌沙虫的沙坑归零会直接吞掉玩家,
        这时输出再高也没用, 必须先打出狂乱逃离。
        """
        return ""

    def minion_death_penalty(self, before, after) -> float:
        """杀死这个 Boss 的爪牙后, 是否反而让它变强; 返回要扣掉的评分."""
        return 0.0

    def buff_on_others(self) -> int:
        """每回合给爪牙增加的力量 (女王): 用来把爪牙标成在变强."""
        return 0

    def steady_block(self) -> int:
        """每回合固定获得的格挡 (女王 +20): 打它要先削这么多, 收益打折."""
        return 0

    def matches(self, enemy) -> bool:
        """实体 id 形如 CEREMONIAL_BEAST_0, 因此按"id + 下划线后缀"匹配."""
        entity_id = getattr(enemy, "entity_id", "") or ""
        for known in self.ids:
            if entity_id == known or entity_id.startswith(f"{known}_"):
                return True
        return getattr(enemy, "name", "") in self.names


@dataclass(frozen=True)
class CeremonialBeastRule(EnemyRule):
    """仪式兽: 血量第一次跌到"横冲直撞"层数以下时被击晕, 并失去全部力量.

    证据:
      * 游戏状态自带说明: ``PLOW_POWER 横冲直撞 150 —
        仪式兽的生命值第一次下降到150或更低时，将其击晕并使其失去所有力量。``
      * 真机录像 logs/run-20261112-202632.jsonl: 血量 170 时仍在攻击,
        降到 146(<=150) 后意图变成 Stun.
      * 灰机 wiki / Spire Codex: 252 血, 一阶段跺地获得 150 层横冲直撞,
        然后每回合横冲直撞(18 伤 +2 力量); 击晕后进入二阶段
        (野兽咆哮给玩家昏眩 -> 踩踏 15 -> 碾碎 17+3 力量).
    """

    ids = ("CEREMONIAL_BEAST",)
    names = ("仪式兽",)
    plow_power = "PLOW_POWER"

    def stun_threshold_hp(self, enemy) -> Optional[int]:
        powers = getattr(enemy, "powers", None) or {}
        plow = powers.get(self.plow_power)
        return int(plow) if isinstance(plow, int) and plow > 0 else None

    def resets_strength_on_stun(self) -> bool:
        # 游戏内说明: "将其击晕并使其失去所有力量".
        return True


@dataclass(frozen=True)
class ShimmeringJaxfruitRule(EnemyRule):
    """闪光贾克斯果: 每回合攻击 3 点并叠 2 点力量, 但力量不加到攻击上.

    证据 (2026-11-14 第一幕第 8 层, logs/run-20261114-064351.jsonl):
      * 意图固定是"攻击 3" + "强化", 状态里的力量从 2 一路涨到 18;
      * 逐回合掉血 1/14/7/1/7/16/7/12/4, 9 回合共 69 点, 平均 7.7;
      * 若力量真的加到攻击上, 第九回合应该是 21 点, 两回合就该打死我们。
    所以它的伤害按固定 3 点建模, 强化只是状态数字。
    """

    ids = ("SHIMMERING_JAXFRUIT", "JAXFRUIT")
    names = ("闪光贾克斯果",)
    strength_per_buff = 2

    def attack_growth_after_buff(self, enemy) -> int:
        return 0


class TheInsatiableRule(EnemyRule):
    """无厌沙虫 (THE_INSATIABLE): 沙坑倒计时归零 = 立刻死亡, 与血量无关.

    证据 (灰机 wiki 无厌沙虫, 2026-11 版本):
      * 第一回合「液化地面」: 获得 4 层沙坑, 抽牌堆与弃牌堆各加入 3 张狂乱逃离;
      * 沙坑每回合 -1, 归零时角色被吞下直接死亡,
        瓶中精灵 / 蜥蜴尾巴这类免死效果都救不了;
      * 狂乱逃离 (状态牌): 打出后沙坑 +1, 但每打出一次, 之后它的耗能 +1;
      * 行为循环: 液化地面 -> 撕扯 (8x2) -> 前扑啃咬 (28) -> 分泌唾液 (+2 力量) -> 撕扯...
    所以这一战不是"打到 0 血", 而是"沙坑见底前必须逃离"。
    """

    ids = ("THE_INSATIABLE",)
    names = ("无厌沙虫",)
    # 沙坑能力在玩家身上的 id 与显示名.
    sandpit_powers = ("SAND_PIT_POWER", "SAND_PIT", "沙坑")
    escape_cards = ("狂乱逃离", "FRENZY_ESCAPE")
    # 沙坑剩这么多时开始逃离. 不能卡在 1~2 才动:
    #   * 狂乱逃离每打出一张, 之后所有狂乱逃离的耗能 +1,
    #     卡到最后一刻可能出现"手里这张费已经不够"的情况;
    #   * 沙坑见底前如果手里恰好没有狂乱逃离, 也没有第二次机会。
    # 留 3 层缓冲: 多花 1 次逃离的代价, 换"永远来得及"。
    danger_threshold = 3

    def sandpit(self, state):
        """玩家身上的沙坑层数; 没有这个能力返回 None.

        返回 None 而不是 0: "没有沙坑" 和 "沙坑已经见底" 是两件相反的事,
        混在一起会让规则在没有沙坑的战斗里也一直要求打出狂乱逃离。
        """
        powers = getattr(getattr(state, "player", None), "powers", None) or {}
        for key in self.sandpit_powers:
            value = powers.get(key)
            if isinstance(value, int):
                return value
        return None

    def required_card(self, state) -> str:
        sandpit = self.sandpit(state)
        if sandpit is None:
            return ""
        return self.escape_cards[0] if sandpit <= self.danger_threshold else ""

    def escape_in_hand(self, state):
        """手里可打出的狂乱逃离; 没有返回 None."""
        battle = getattr(state, "battle", None)
        if battle is None:
            return None
        for card in battle.hand:
            if card.name.rstrip("+") in self.escape_cards and card.can_play:
                return card
        return None


class QueenRule(EnemyRule):
    """女王 (QUEEN): 爪牙存活时她不攻击, 但每回合给爪牙 +1 力量、自己 +20 格挡.

    证据 (灰机 wiki 女王, 2026-11 版本):
      * 开局场上有一只爪牙 火炬头聚合体 (199 血, 冲撞 18 x2 / 光束 8x3 循环);
      * 只要聚合体在场: 女王第 1 回合给玩家 3 层魂缚锁链, 第 2 回合给 99 层
        脆弱 / 虚弱 / 易伤, 第 3 回合起永远用"为我尽力燃烧吧":
        **聚合体 +1 力量、女王自己 +20 格挡**, 她自己不攻击;
      * 聚合体死亡 -> 女王立刻激怒 (+2 力量), 之后 将头砍下 (3x5) / 处决 (15) 循环。

    因此这一战的核心不是"打谁", 而是两个资源:
      * 聚合体每回合变强 -> 拖不得, 而且它才是伤害来源;
      * 女王每回合 20 格挡 -> 打她等于先削 20 点, 不适合当首要目标。
    优先处理会成长的爪牙, 女王留到爪牙死后集中输出, 是这套机制的顺势打法。
    """

    ids = ("QUEEN",)
    names = ("女王",)
    # 女王在爪牙存活时每回合给自己多少格挡.
    steady_block_per_turn = 20
    # 女王每回合给爪牙加多少力量.
    minion_strength_per_turn = 1

    def buff_on_others(self) -> int:
        """每回合给爪牙增加的力量: 用来把爪牙标成"在变强"."""
        return self.minion_strength_per_turn

    def steady_block(self) -> int:
        """每回合固定获得的格挡: 用于评估"打它有多亏"."""
        return self.steady_block_per_turn


class RuleBook:
    """敌人 id → 规则的注册表; 默认不含任何规则."""

    def __init__(self, rules: Optional[list[EnemyRule]] = None) -> None:
        self.rules = list(rules or [])

    @classmethod
    def with_defaults(cls) -> "RuleBook":
        return cls([CeremonialBeastRule(), ShimmeringJaxfruitRule(), TheInsatiableRule(),
                    QueenRule()])

    def rule_for(self, enemy) -> Optional[EnemyRule]:
        for rule in self.rules:
            if rule.matches(enemy):
                return rule
        return None

    def minion_death_penalty(self, before, after) -> float:
        """击杀爪牙的总惩罚 (来自各 Boss 规则)."""
        total = 0.0
        for rule in self.rules:
            total += rule.minion_death_penalty(before, after)
        return total

    def minion_growth(self, state) -> int:
        """Boss 每回合给爪牙加的力量: 有爪牙存活时, 爪牙算"在变强"."""
        battle = getattr(state, "battle", None)
        if battle is None:
            return 0
        minions = [e for e in battle.alive_enemies if getattr(e, "powers", {}).get("MINION_POWER")]
        if not minions:
            return 0
        growth = 0
        for enemy in battle.alive_enemies:
            rule = self.rule_for(enemy)
            if rule is not None:
                growth += rule.buff_on_others()
        return growth

    def steady_block(self, state) -> int:
        """当前存活敌人里最大的"每回合格挡" (女王 20): 打它要先削这么多."""
        battle = getattr(state, "battle", None)
        if battle is None:
            return 0
        total = 0
        for enemy in battle.alive_enemies:
            rule = self.rule_for(enemy)
            if rule is not None:
                total += rule.steady_block()
        return total

    def required_card(self, state) -> tuple[str, object]:
        """本回合是否必须打出某张牌; 返回 (牌名, 规则) 或 ("", None)."""
        battle = getattr(state, "battle", None)
        if battle is None:
            return "", None
        for enemy in battle.alive_enemies:
            rule = self.rule_for(enemy)
            if rule is None:
                continue
            name = rule.required_card(state)
            if name:
                return name, rule
        return "", None

    def stun_bonus(self, sim, enemy_index: int) -> float:
        """该敌人当前是否已经在击晕阈值以下; 返回加成分数 (跨阈值才计)."""
        if not self.rules:
            return 0.0
        enemies = getattr(sim, "enemies", ())
        if enemy_index >= len(enemies):
            return 0.0
        enemy = enemies[enemy_index]
        rule = self.rule_for(enemy)
        if rule is None:
            return 0.0
        threshold = rule.stun_threshold_hp(enemy)
        if threshold is None or not enemy.alive:
            return 0.0
        return STUN_TURN_VALUE if enemy.hp <= threshold else 0.0

    def crossing_bonus(self, before, after) -> float:
        """比较方案前后的模拟状态, 只奖励"刚刚跨过阈值"的敌人.

        阈值取自状态 (例如仪式兽的横冲直撞层数), 因此不随版本写死.
        若敌人被击晕时还会清空力量, 力量越高这一下越值钱.
        """
        total = 0.0
        for i, enemy in enumerate(getattr(after, "enemies", ())):
            rule = self.rule_for(enemy)
            if rule is None:
                continue
            threshold = rule.stun_threshold_hp(enemy)
            if threshold is None:
                continue
            now_below = enemy.hp <= threshold and enemy.alive
            was_above = i >= len(before.enemies) or before.enemies[i].hp > threshold
            if not (was_above and now_below):
                continue
            total += STUN_TURN_VALUE
            if rule.resets_strength_on_stun() and i < len(before.enemies):
                strength = (getattr(before.enemies[i], "powers", None) or {}).get("STRENGTH_POWER")
                if isinstance(strength, int) and strength > 0:
                    total += strength * STRENGTH_RESET_VALUE
        return total


@dataclass(frozen=True)
class _Shim:
    """只带名称与 id 的轻量对象, 给规则做匹配用."""

    name: str = ""
    entity_id: str = ""


_BOOK = RuleBook.with_defaults()


def attack_growth(name: str, entity_id: str, *, buff_intent: bool) -> int:
    """这个怪物本回合强化后, 下回合攻击会涨多少点.

    未知怪物返回 0: 不假设它会变强, 也就不会因为"有 Buff 意图"就去抢输出.
    """
    if not buff_intent:
        return 0
    shim = _Shim(name=name, entity_id=entity_id)
    rule = _BOOK.rule_for(shim)
    return rule.attack_growth_after_buff(shim) if rule is not None else 0
