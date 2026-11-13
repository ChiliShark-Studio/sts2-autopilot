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


class RuleBook:
    """敌人 id → 规则的注册表; 默认不含任何规则."""

    def __init__(self, rules: Optional[list[EnemyRule]] = None) -> None:
        self.rules = list(rules or [])

    @classmethod
    def with_defaults(cls) -> "RuleBook":
        return cls([CeremonialBeastRule()])

    def rule_for(self, enemy) -> Optional[EnemyRule]:
        for rule in self.rules:
            if rule.matches(enemy):
                return rule
        return None

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
