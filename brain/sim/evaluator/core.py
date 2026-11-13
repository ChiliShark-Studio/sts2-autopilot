"""固定洗牌预算下的四维观测；复用游戏所用的启发式决策，不调模型。"""

from __future__ import annotations

import copy
import math
import statistics
from typing import Any

from ...contracts import DecisionKind, DecisionRequest
from ...cards import EffectKind, Event, TriggeredEffect, card_effects
from ...model import Card, GameState
from ...combat.search import ENERGY_VALUE, HP_WEIGHT_FULL, HP_WEIGHT_LOW, decide_state
from ...agents.select import SelectAgent
from ...build.select import BuildSelect
from ...combat.select import CombatSelect
from ...combat.effects import resolve_cost
from ...upgrades import choose_upgrade
from ..engine import Fight, Intent, Monster
from .input import EvaluationInput, card_type, parse


class _ObservedFight(Fight):
    """工具自己的观测实例，不修改共享模拟器及线上战斗行为。"""

    def __init__(self, spec: EvaluationInput, seed: int, enemies: list[Monster] | None = None) -> None:
        if enemies is not None and len(enemies) != 1:
            raise ValueError("当前评估器只支持单体遭遇，不能把群体效果降级成单体结算")
        self.spec = spec
        self.benchmark = enemies is None
        deck = copy.deepcopy(spec.deck)
        super().__init__(deck, enemies if enemies is not None else [
            Monster("单体评估木桩", spec.enemy_hp, spec.enemy_hp, [Intent("attack", spec.incoming[0])])],
                         seed=seed, hp=spec.hp, max_hp=spec.hp,
                         max_energy=spec.energy[0], strength=spec.strength, block=spec.block)
        self.original_ids = {c.id for c in deck}
        self.original_names = {c.id: c.name for c in deck}
        self.upgraded_ids: set[str] = set()
        self.seen: set[str] = set()
        self.first_cycle: int | None = None
        self.first_draw: dict[str, int] = {}
        self.first_play: dict[str, int] = {}
        self.first_trigger: dict[str, int] = {}
        self.group_turns: dict[str, int] = {}
        self.active: dict[str, Any] = {}
        self.records: list[dict] = []
        self.row: dict = {}
        self._status_hits = 0
        self.draw_per_vulnerable = 0
        self._vulnerable_draw = 0
        self._vulnerable_target: Monster | None = None
        self._topdeck_pending = 0
        self.per_turn_energy = 0
        self.temporary_strength = 0
        self.no_energy_gain = False
        self.effect_sources: dict[str, set[str]] = {}
        self.hellraiser = False
        self.panache: list[list[int]] = []
        self._start_energy = 0

    def _draw(self, count: int) -> None:
        # 共享引擎已重置能量并结算回合初力量；抽牌触发自动出牌前先补上产能。
        self.energy += self._start_energy
        self._start_energy = 0
        if self._vulnerable_draw:
            # 伤害先结算；已死亡的目标不能再被给予易伤并触发凶恶。
            if self._vulnerable_target is None or not self._vulnerable_target.alive:
                count -= self._vulnerable_draw
            else:
                self._trigger("draw_per_vulnerable")
            self._vulnerable_draw = 0
        pending, self._topdeck_pending = self._topdeck_pending, 0
        for _ in range(count):
            if self.over or len(self.hand) >= 10:
                break
            before = len(self.hand)
            super()._draw(1)
            if len(self.hand) == before:
                break
            card = self.hand[-1]
            self.row["drawn"] += 1
            self.seen.add(card.id)
            self.first_draw.setdefault(self.original_names.get(card.id, card.name), self.turn)
            if self.first_cycle is None and self.seen >= self.original_ids:
                self.first_cycle = self.turn
            if self.hellraiser and getattr(card, "strike", False):
                # 昏眩同样限制自动出牌；被阻止的牌进入弃牌堆，不扣能量。
                if self.ringing_active and self.turn_cards_played >= 1:
                    self.discard.append(self.hand.pop())
                    self.row["auto_blocked"].append(card.name)
                else:
                    self._trigger("hellraiser")
                    self.play(len(self.hand) - 1, automatic=True)
        for _ in range(pending):
            if self.over or not self.hand:
                break
            raw = self.to_state()
            raw["state_type"] = "hand_select"
            raw["hand_select"] = {"prompt": "选择一张手牌放到你的抽牌堆顶。",
                                  "cards": raw["player"]["hand"]}
            decision = SelectAgent(BuildSelect(), CombatSelect()).decide(DecisionRequest(
                kind=DecisionKind.SELECT, state=GameState.parse(raw), scene="hand_select"))
            if decision.action is None or decision.action.kind != "combat_select_card":
                raise ValueError("放回抽牌堆顶时未得到合法选牌动作")
            card = self.hand.pop(decision.action.index)
            self.draw_pile.append(card)
            self.row["topdecked"].append(card.name)

    def _trigger(self, field: str) -> None:
        for name, card in self.active.items():
            if getattr(card, field):
                self.first_trigger.setdefault(name, self.turn)

    def _observe_effects(self, start: int) -> None:
        for effect in self.effect_log[start:]:
            for name in self.effect_sources.get(effect.power_id, ()):
                self.first_trigger.setdefault(name, self.turn)

    def start_turn(self) -> None:
        remaining = self.energy if self._started and self.spec.carry_energy else 0
        self.max_energy = self.spec.energy[self.turn - 1]
        self.draw_per_turn = self.spec.draw[self.turn - 1]
        if self.benchmark:
            self.monsters[0].script = [Intent("attack", self.spec.incoming[self.turn - 1])]
        self.row = {"turn": self.turn, "damage": 0, "block_generated": 0,
                    "effective_block": 0, "weak_mitigation": 0, "enemy_hp_loss": 0,
                    "self_hp_cost": 0, "status_hp_loss": 0, "status_block": 0,
                    "energy_start": self.max_energy + remaining,
                    "energy_generated": 0, "energy_spent": 0, "drawn": 0, "played": [],
                    "upgraded": [], "topdecked": [], "auto_played": [], "auto_blocked": []}
        self.energy = self.max_energy + remaining
        self._start_energy = remaining + self.per_turn_energy
        self.row["energy_generated"] += self.per_turn_energy
        if self.per_turn_energy:
            self._trigger("per_turn_energy")
        if self._started and self.keep_block and self.block:
            self._trigger("keep_block")
        before_effects = len(self.effect_log)
        super().start_turn()
        self._observe_effects(before_effects)

    def play(self, hand_index: int, target: str | None = None, *, automatic: bool = False) -> bool:
        if not 0 <= hand_index < len(self.hand):
            return False
        card = self.hand[hand_index]
        kind = self.spec.types[card.id]
        free = automatic or (self.corruption and kind == "skill")
        cost = 0 if free else resolve_cost(card.cost, card.cost_x, self.energy)
        if (not card.playable or cost > self.energy or self.over
                or (self.ringing_active and self.turn_cards_played >= 1)):
            return False
        before_hp, before_block = self.hp, self.block
        before_damage = self.monsters[0].hp
        before_exhaust = self.exhausted
        before_drawn = self.row["drawn"]
        before_effects = len(self.effect_log)
        counters = {key: self.row[key] for key in (
            "damage", "block_generated", "self_hp_cost")}
        corruption = self.corruption
        # 共享引擎以零伤害推断技能；这里按输入类型区分技能、攻击和能力牌。
        if kind != "skill":
            self.corruption = False
        self._vulnerable_target = next((m for i, m in enumerate(self.monsters)
            if m.alive and (target is None or (m.entity_id or f"E{i}") == target)), None)
        self._vulnerable_draw = self.draw_per_vulnerable if card.vulnerable else 0
        self._topdeck_pending = getattr(card, "topdeck", 0)
        original_draw = card.draw
        original_cost, original_damage, original_energy = card.cost, card.damage, card.energy
        original_cost_x = card.cost_x
        card.cost = cost
        card.cost_x = original_cost_x and not free
        card.damage += getattr(card, "per_strike", 0) * sum(c.strike for c in self.spec.deck)
        gained = card.energy + getattr(card, "energy_per_attack", 0) * sum(
            self.spec.types.get(c.id) == "attack" for c in self.hand if c is not card)
        card.energy = 0 if self.no_energy_gain else gained
        gained = card.energy
        # 在共享引擎的抽牌阶段触发，当前打出的牌此时还没有进入弃牌堆。
        card.draw += self._vulnerable_draw
        try:
            ok = super().play(hand_index, target)
        finally:
            card.draw = original_draw
            card.cost, card.damage, card.energy = original_cost, original_damage, original_energy
            card.cost_x = original_cost_x
            self._vulnerable_draw = self._topdeck_pending = 0
            self._vulnerable_target = None
        self.corruption = self.corruption or corruption
        if not ok:
            return False
        self.dexterity += getattr(card, "dexterity", 0)
        temporary = getattr(card, "temporary_strength", 0)
        self.strength += temporary
        self.temporary_strength += temporary
        self.per_turn_energy += getattr(card, "per_turn_energy", 0)
        self.no_energy_gain = self.no_energy_gain or getattr(card, "no_energy_gain", False)
        self.hellraiser = self.hellraiser or getattr(card, "hellraiser", False)
        self.draw_per_vulnerable += getattr(card, "draw_per_vulnerable", 0)
        if kind == "power":
            # 能力牌留在能力区，不应在下一轮被重洗、重复叠加。
            self.discard[:] = [c for c in self.discard if c.id != card.id]
        if card.name.rstrip("+") == "武装":
            self._upgrade_hand(all_cards=card.name.endswith("+"))
        # 嵌套抽牌可能自动打出其他牌；区间差值扣除已记录的子动作，避免重复统计。
        self.row["energy_spent"] += cost
        self.row["energy_generated"] += gained
        self.row["self_hp_cost"] += max(0, before_hp - self.hp - (
            self.row["self_hp_cost"] - counters["self_hp_cost"]))
        self.row["block_generated"] += max(0, self.block - before_block - (
            self.row["block_generated"] - counters["block_generated"]))
        self.row["damage"] += max(0, min(before_damage, before_damage - self.monsters[0].hp) - (
            self.row["damage"] - counters["damage"]))
        original_name = self.original_names.get(card.id, card.name)
        effects = card.definition.effects if card.definition is not None else card_effects(
            per_turn_strength=card.per_turn_strength, block_per_attack=card.block_per_attack)
        for effect in effects:
            if isinstance(effect, TriggeredEffect):
                self.effect_sources.setdefault(effect.power_id, set()).add(original_name)
        self.row["played"].append(original_name)
        if automatic:
            self.row["auto_played"].append(original_name)
        self.first_play.setdefault(original_name, self.turn)
        if self.exhausted > before_exhaust:
            self._trigger("block_per_exhaust")
            self._trigger("draw_per_exhaust")
        if card.block:
            self._trigger("juggernaut")
            self._trigger("double_first_block")
        if card.hp_cost:
            self._trigger("rupture")
        if corruption and kind == "skill":
            self._trigger("corruption")
        if before_damage > self.monsters[0].hp and self.monsters[0].vulnerable:
            self._trigger("cruelty")
        if (card.strength or gained or temporary or getattr(card, "dexterity", 0) or card.vulnerable or card.weak
                or self.row["drawn"] > before_drawn or self.block > before_block
                or self.monsters[0].hp < before_damage
                or (getattr(card, "topdeck", 0) and self.row["topdecked"])):
            self.first_trigger.setdefault(original_name, self.turn)
        if kind == "power":
            self.active[original_name] = card
        self._observe_effects(before_effects)
        if not self.over:
            for instance in self.panache:
                instance[1] += 1
                if instance[1] == 5:
                    instance[1] = 0
                    self._power_damage(instance[0])
                    self._trigger("panache")
        if getattr(card, "panache", 0):
            # 神气制胜不计自己；已存在的实例仍会计入这次出牌。
            self.panache.append([card.panache, 0])
        available = set(self.active) | set(self.row["played"])
        for name, required in self.spec.groups.items():
            if set(required) <= available:
                self.group_turns.setdefault(name, self.turn)
        return True

    def _power_damage(self, amount: int) -> None:
        enemy = self.monsters[0]
        if not enemy.alive:
            return
        if enemy.slippery:
            amount = min(1, amount)
            enemy.slippery -= 1
        absorbed = min(enemy.block, amount)
        enemy.block -= absorbed
        lost = min(enemy.hp, max(0, amount - absorbed))
        enemy.hp -= lost
        enemy.on_damaged(lost)
        self.row["damage"] += lost

    def _upgrade_hand(self, all_cards: bool) -> None:
        eligible = [i for i, card in enumerate(self.hand)
                    if card.id in self.spec.upgrades and card.id not in self.upgraded_ids]
        if eligible and not all_cards:
            hand = self.to_state()["player"]["hand"]
            weight = HP_WEIGHT_FULL + (HP_WEIGHT_LOW - HP_WEIGHT_FULL) * (1 - max(0.0, min(1.0, self.hp / self.max_hp)))
            position = choose_upgrade([Card.parse(hand[i]) for i in eligible], weight, ENERGY_VALUE)
            eligible = [eligible[position]] if position is not None else []
        for i in eligible:
            card = self.hand[i]
            self.hand[i] = copy.deepcopy(self.spec.upgrades[card.id])
            self.upgraded_ids.add(card.id)
            self.row["upgraded"].append({"from": card.name, "to": self.hand[i].name})

    def _take_damage(self, damage: int) -> None:
        absorbed = min(self.block, damage)
        before_hp = self.hp
        super()._take_damage(damage)
        source = "status_hp_loss" if self._status_hits else "enemy_hp_loss"
        self.row[source] += min(max(0, before_hp), max(0, before_hp - self.hp))
        self.row["status_block" if self._status_hits else "effective_block"] += absorbed
        if self._status_hits:
            self._status_hits -= 1
        elif self.monsters[0].weak:
            enemy = self.monsters[0]
            intent = enemy.intent()
            base = intent.params.get("damage", 0) if intent.type == "custom" else intent.damage
            baseline = max(0, base + enemy.strength)
            self.row["weak_mitigation"] += max(0, baseline - damage)

    def finish_turn(self, ended: bool) -> None:
        self.row["energy_remaining"] = self.energy
        self.row["block_before_enemy"] = self.block
        self.row["unaffordable_cards"] = sum(
            c.playable and c.cost > self.energy
            and not (self.corruption and self.spec.types[c.id] == "skill") for c in self.hand)
        if ended:
            self.strength -= self.temporary_strength
            self.temporary_strength = 0
            self.no_energy_gain = False
            for instance in self.panache:
                instance[1] = 0
            self._status_hits = sum(bool(c.end_turn_damage) for c in self.hand)
            super().end_turn()
            self.effect_sources = {identity: names for identity, names in self.effect_sources.items()
                                   if identity in self.effect_powers}
        self.row["hp_remaining"] = max(0, self.hp)
        self.records.append(self.row)

    def end_turn(self) -> None:
        self.finish_turn(True)

    def to_state(self) -> dict:
        raw = super().to_state()
        raw["player"]["exhaust_pile_count"] = self.exhausted
        raw["player"]["draw_pile_count"] = len(self.draw_pile)
        raw["player"]["discard_pile_count"] = len(self.discard)
        raw["player"]["max_energy"] = self.max_energy
        # 原牌组仅提供公开的费用、攻击密度和命中次数，不暴露抽牌堆顺序。
        raw["player"]["deck"] = [{
            "name": c.name, "type": self.spec.types[c.id], "cost": str(c.cost),
            "damage": c.damage + getattr(c, "per_strike", 0) * sum(x.strike for x in self.spec.deck),
            "hits": c.hits, "block": c.block, "target_type": c.target,
        } for c in self.spec.deck]
        for identity, name, amount in (
                ("NO_ENERGY_GAIN_POWER", "不能获得能量", int(self.no_energy_gain)),
                ("PYRE_POWER", "薪火之源", self.per_turn_energy),
                ("SETUP_STRIKE_POWER", "预备打击", self.temporary_strength)):
            if amount:
                raw["player"]["status"].append({"id": identity, "name": name, "amount": amount})
        for entry, card in zip(raw["player"]["hand"], self.hand):
            definition = card.definition
            kind = self.spec.types.get(card.id, card_type(card) if card.playable else "status")
            cost = 0 if self.corruption and kind == "skill" else card.cost
            desc = [card.description] if card.description else []
            if card.hits_x:
                desc.append(f"造成{card.damage}点伤害X次。")
            if card.scales_with_block:
                desc.append("造成你当前格挡值的伤害。")
            if card.per_vulnerable:
                desc.append(f"该敌人身上每有一层易伤就额外造成{card.per_vulnerable}点伤害。")
            if card.per_exhaust:
                desc.append(f"你的消耗牌堆中每有一张牌，伤害增加{card.per_exhaust}。")
            if card.exhaust:
                desc.append("消耗。")
            if getattr(card, "draw_per_vulnerable", 0):
                desc.append(f"每当你给予易伤时，抽{card.draw_per_vulnerable}张牌。")
            if getattr(card, "topdeck", 0):
                desc.append(f"将手牌中的{card.topdeck}张牌放到你的抽牌堆顶。")
            if card.per_turn_strength and (definition is None or card.per_turn_strength != definition.amount(
                    Event.TURN_START, EffectKind.GAIN_STRENGTH)):
                desc.append(f"在你的回合开始时，获得{card.per_turn_strength}点力量。")
            if getattr(card, "temporary_strength", 0):
                desc.append(f"在本回合内获得{card.temporary_strength}点力量。")
            if getattr(card, "hellraiser", False):
                desc.append("每当你抽到名字中有打击的牌时，对一名随机敌人打出这张牌。")
            if card.block_per_attack and (definition is None or card.block_per_attack != definition.amount(
                    Event.ATTACK_RESOLVED, EffectKind.GAIN_BLOCK)):
                desc.append(f"本回合内每打出一张攻击牌，获得{card.block_per_attack}点格挡。")
            if getattr(card, "panache", 0):
                desc.append(f"每当你在一回合内打出五张牌时，对所有敌人造成{card.panache}点伤害。")
            if getattr(card, "no_energy_gain", False):
                desc.append("本回合不能再获得能量。")
            if getattr(card, "energy_per_attack", 0):
                desc.append("你的手牌中每有一张攻击牌，就获得1点能量。")
            if card.name.rstrip("+") == "武装":
                desc.append("升级你手牌中的所有牌。" if card.name.endswith("+") else "升级你手牌中的一张牌。")
            # 能量由搜索逐步检查，避免初始买不起的牌在产能后仍被缓存为不可打出。
            entry.update(type=kind, cost="X" if card.cost_x and not (self.corruption and kind == "skill") else str(cost), can_play=card.playable and not (
                         self.ringing_active and self.turn_cards_played >= 1),
                         strength=card.strength + getattr(card, "temporary_strength", 0),
                         dexterity=getattr(card, "dexterity", 0),
                         energy=0 if self.no_energy_gain else card.energy + getattr(card, "energy_per_attack", 0) * sum(
                             self.spec.types.get(c.id) == "attack" for c in self.hand if c is not card),
                         is_upgraded=card.name.endswith("+"), description="".join(desc))
            if kind == "attack":
                enemy = self.monsters[0]
                base = self.block if card.scales_with_block else card.damage
                base += card.per_vulnerable * enemy.vulnerable + card.per_exhaust * self.exhausted
                base += getattr(card, "per_strike", 0) * sum(c.strike for c in self.spec.deck)
                entry["damage"] = base
                damage = max(0, base + self.strength)
                if enemy.vulnerable:
                    damage = int(damage * (1.5 + self.cruelty))
                if enemy.has_slow:
                    damage = int(damage * (1 + .1 * enemy.slow))
                entry["damage_by_target"] = {enemy.entity_id or "E0": damage}
        return raw


def _stats(values: list[int | float]) -> dict:
    ordered = sorted(values)
    return {"samples": len(values), "mean": round(statistics.mean(values), 3) if values else None,
            "median": statistics.median(values) if values else None,
            "p10": ordered[max(0, math.ceil(len(values) * .1) - 1)] if values else None,
            "p90": ordered[max(0, math.ceil(len(values) * .9) - 1)] if values else None}


def _timing(values: list[int | None]) -> dict:
    return {**_stats([v for v in values if v is not None]),
            "not_observed": sum(v is None for v in values), "total_samples": len(values)}


def evaluate(raw: dict) -> dict:
    """输入牌组与显式资源，返回四维报告；不调用 LLM，不修改输入。"""
    spec = parse(raw)
    report = {
        "status": "unsupported" if spec.missing else "ok",
        "coverage": {"missing": spec.missing, "assumptions": spec.assumptions,
                     "scope": "受控单体木桩，复用当前单回合束搜索；不代表完整游戏或通关概率",
                     "policy_limitations": "搜索不完整预估抽牌后续和消耗/能力触发，结果反映当前策略的实际执行"},
        "budget": {"runs": spec.runs, "turns": spec.turns, "seed": spec.seed},
        "context": {"character": spec.character, "relics": spec.relics,
                    "external_energy_by_turn": spec.energy, "external_draw_by_turn": spec.draw,
                    "energy_source": "输入总量（含遗物）" if "energy_by_turn" in raw else "基础能量加显式遗物效果",
                    "incoming_by_turn": spec.incoming, "enemy_hp": spec.enemy_hp, "hp": spec.hp},
        "aggregation": "逐回合仅统计完整观测的回合；累计和平均指标仅含观察窗口齐全的样本。"
                       "提前获胜、死亡和未完成另列，不补零；各回合样本集可能不同，不能只比均值。",
        "dimensions": {"damage": None, "defense": None, "cost": None, "startup": None},
        "model_guidance": (
            "分别制定伤害、防御、费用、启动的通关准备目标，不合成总分。结合角色特点、"
            "完整牌组、遗物、逐回合能量及已知下一阶段遭遇，说明当前值、目标依据、缺口和取舍。"
            "费用按 external_energy_by_turn 加实际牌内产能判断，已计入的遗物不能重复相加；"
            "首回合和条件产能不能当作每回合固定产能。铁甲战士要同时检查力量收益、"
            "消耗触发、格挡转伤害与启动期间生存，只有现有资源支持时才发展对应配合。"
            "启动先看未观测比例，再看已启动样本的时间分布；剩余能量和未打出的高费牌"
            "只是观测，不直接等于浪费或费用不足。四个维度分别写出当前观测、下一阶段目标、"
            "证据、拟补强组件及代价，目标须适配同一套能量预算。"
            "木桩结果不能当作精英安全或通关证明；未知规则或未知遭遇时，目标数字标为待验证，"
            "不得编造模拟结果或固定全局合格线。"
        ),
    }
    if spec.missing:
        return report
    fights, outcomes = [], {"horizon": 0, "win": 0, "death": 0, "incomplete": 0}
    for seed in range(spec.seed, spec.seed + spec.runs):
        fight = _ObservedFight(spec, seed)
        outcome = "horizon"
        for turn in range(1, spec.turns + 1):
            fight.turn = turn
            fight.start_turn()
            for _ in range(100):
                if fight.over:
                    break
                decision = decide_state(fight.to_state())
                action = decision.action
                if action is None or action.kind not in {"play_card", "end_turn"}:
                    outcome = "incomplete"
                    break
                if action.kind == "end_turn":
                    break
                if not fight.play(action.card.index, action.target):
                    outcome = "incomplete"
                    break
                if fight.over:
                    break
            else:
                outcome = "incomplete"
            fight.finish_turn(ended=not fight.over and outcome != "incomplete")
            fight.records[-1]["complete"] = outcome != "incomplete"
            if fight.over:
                outcome = "win" if fight.won else "death"
            if outcome != "horizon":
                break
        fights.append(fight)
        outcomes[outcome] += 1
    report["outcomes"] = outcomes
    if outcomes["incomplete"]:
        report["status"] = "incomplete"
    by_turn = []
    for turn in range(spec.turns):
        rows = [f.records[turn] for f in fights if len(f.records) > turn and f.records[turn]["complete"]]
        by_turn.append({key: _stats([r[key] for r in rows]) for key in (
            "damage", "block_generated", "effective_block", "weak_mitigation", "enemy_hp_loss",
            "self_hp_cost", "status_hp_loss", "status_block", "energy_start", "energy_generated", "energy_spent",
            "energy_remaining", "unaffordable_cards", "drawn")})
        by_turn[-1]["upgraded_cards"] = _stats([len(r["upgraded"]) for r in rows])
        by_turn[-1]["automatic_cards"] = _stats([len(r["auto_played"]) for r in rows])
        by_turn[-1]["automatic_blocked"] = _stats([len(r["auto_blocked"]) for r in rows])
    report["dimensions"] = {
        "damage": {"by_turn": [r["damage"] for r in by_turn],
                   "first_two_turns": _stats([sum(r["damage"] for r in f.records[:2])
                                              for f in fights if len(f.records) >= 2 and
                                              all(r["complete"] for r in f.records[:2])]),
                   "horizon_average": _stats([sum(r["damage"] for r in f.records) / spec.turns
                                               for f in fights if len(f.records) == spec.turns and
                                               all(r["complete"] for r in f.records)])},
        "defense": {key: [r[key] for r in by_turn] for key in (
            "block_generated", "effective_block", "weak_mitigation", "enemy_hp_loss", "self_hp_cost",
            "status_hp_loss", "status_block")},
        "cost": {"printed_average": round(statistics.mean(c.cost for c in spec.deck if not c.cost_x), 3)
                 if any(not c.cost_x for c in spec.deck) else None,
                 **{key: [r[key] for r in by_turn] for key in (
                     "energy_start", "energy_generated", "energy_spent", "energy_remaining", "unaffordable_cards")}},
        "startup": {"first_cycle": _timing([f.first_cycle for f in fights]),
                    "cards_drawn_by_turn": [r["drawn"] for r in by_turn],
                    "cards_upgraded_by_turn": [r["upgraded_cards"] for r in by_turn],
                    "automatic_cards_by_turn": [r["automatic_cards"] for r in by_turn],
                    "automatic_blocked_by_turn": [r["automatic_blocked"] for r in by_turn],
                    "key_cards": {name: {"first_draw": _timing([f.first_draw.get(name) for f in fights]),
                                         "first_play": _timing([f.first_play.get(name) for f in fights]),
                                         "first_trigger": _timing([f.first_trigger.get(name) for f in fights])}
                                  for name in spec.key_cards},
                    "groups": {name: _timing([f.group_turns.get(name) for f in fights]) for name in spec.groups},
                    "group_definition": "已激活的能力牌与本回合已打出的牌覆盖指定组件；不证明无限或稳定通关"},
    }
    if any(c.cost_x for c in spec.deck):
        report["dimensions"]["cost"].update(
            x_cost_cards=sum(c.cost_x for c in spec.deck), printed_average_scope="仅固定费牌；X 费不按零费计入平均值")
    return report


def fight_boss(raw: dict, encounter: str, max_turns: int = 50) -> dict:
    """同一构筑交给现有竞技场打 Boss，沿用本工具的升级与资源规则。"""
    from ..arena import BatchResult, _play_fight
    from ..scenarios import encounter as make_encounter

    if encounter not in {"vantom", "ceremonial_beast"}:
        raise ValueError("当前只接入单体 Boss 场景 vantom、ceremonial_beast")
    request = copy.deepcopy(raw)
    request["turns"] = max_turns
    spec = parse(request)
    coverage = {"missing": spec.missing,
                "assumptions": [a for a in spec.assumptions if not a.startswith("默认敌人为单体木桩")],
                "scope": "仓库现有近似 Boss 脚本，不代表真实游戏胜率；战后治疗不计入胜利剩余血量"}
    if spec.missing:
        return {"status": "unsupported", "coverage": coverage}
    results = []
    observations = []
    for seed in range(spec.seed, spec.seed + spec.runs):
        fight = _ObservedFight(spec, seed, make_encounter(encounter))
        result = _play_fight(fight, max_turns=max_turns, policy=decide_state)
        if not fight.records or fight.records[-1] is not fight.row:
            fight.finish_turn(False)
        results.append(result)
        observations.append({**result.to_dict(), "seed": seed,
                             "hp_loss": spec.hp - result.hp_left,
                             "enemy_hp_left": sum(max(0, e.hp) for e in fight.monsters),
                             "cards_upgraded": len(fight.upgraded_ids),
                             "automatic_plays": sum(len(r["auto_played"]) for r in fight.records),
                             "energy_generated": sum(r["energy_generated"] for r in fight.records),
                             "key_cards": {name: {"first_play": fight.first_play.get(name),
                                                   "first_trigger": fight.first_trigger.get(name)}
                                           for name in spec.key_cards}})
    batch = BatchResult(results)
    return {"status": "incomplete" if any(r.outcome.startswith("incomplete:") for r in results) else "ok",
            "encounter": encounter, "coverage": coverage,
            "budget": {"runs": spec.runs, "max_turns": max_turns, "seed": spec.seed},
            "summary": batch.summary(), "observations": observations,
            "damage_note": "原引擎 damage_taken 可含致死溢出；实际生命损失请看 observations.hp_loss"}


def compare(current: dict, candidate: dict) -> dict:
    """比较加入候选前后的四维报告，保留结果分布，不输出综合排名。"""
    changed = copy.deepcopy(current)
    changed["deck"] = list(changed["deck"]) + [copy.deepcopy(candidate)]
    before, after = evaluate(current), evaluate(changed)
    delta = {}
    if before["status"] == after["status"] == "ok":
        for dimension in ("damage", "defense", "cost", "startup"):
            delta[dimension] = {}
            for key, left in before["dimensions"][dimension].items():
                right = after["dimensions"][dimension].get(key)
                if isinstance(left, dict) and "mean" in left:
                    delta[dimension][key] = (round(right["mean"] - left["mean"], 3)
                                              if left["mean"] is not None and right["mean"] is not None else None)
                elif isinstance(left, list) and left and isinstance(left[0], dict):
                    delta[dimension][key] = [round(b["mean"] - a["mean"], 3)
                                              if a["mean"] is not None and b["mean"] is not None else None
                                              for a, b in zip(left, right)]
    return {"before": before, "after": after, "mean_delta": delta,
            "comparison": "预算、策略和种子相同；均值差不是显著性结论，需同时检查样本数和终止结果；"
                          "牌组改变会改变洗牌映射，同种子不是完全相同牌序"}
