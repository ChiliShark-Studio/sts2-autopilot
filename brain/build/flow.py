"""构筑界面分派：供 BuildAgent 处理奖励、事件、篝火、宝箱、卡牌包和遗物选择。

商店决策委托 build.shop，卡牌评分复用 build.scoring；本文件保留其他构筑场景的现有处理。
每次仅返回一个动作，由游戏循环执行后重新读取状态；模型失败停止，离线运行使用现有启发式。

流程基于真实对局中发现的两条规则:

* ``skip_card_reward`` 不会消耗卡牌奖励条目, 再次领取会重开选牌界面,
  看起来像卡住. 正确顺序是先领取实际物品, 只打开一次选牌, 选择后继续前进.
* 每次只返回一个合法动作，购物和药水替换后重新读取状态。
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Optional

from ..actions import (
    AdvanceDialogue,
    ChooseEventOption,
    ClaimReward,
    ClaimTreasureRelic,
    ConfirmBundleSelection,
    DiscardPotion,
    Proceed,
    RestOption,
    SelectBundle,
    SelectCardReward,
    SelectRelic,
    SkipCardReward,
    SkipRelicSelection,
)
from ..contracts import WAIT, Decision, DecisionRequest, Source
from ..strategy.choice import LLM_ATTEMPTS, choose, no_decision
from ..strategy.context import StrategyContext
from .scoring import _card_dict_value, card_score
from .shop import ShopDecision

SKIP_THRESHOLD = 2.0

_HP_LOSS = re.compile(r"失去\s*(\d+)\s*点(?:最大)?生命")
_MAX_HP_LOSS = re.compile(r"失去\s*(\d+)\s*点最大生命")
_HEAL_EVENT = re.compile(r"回复\s*(\d+)\s*点生命")
_MAX_HP_GAIN = re.compile(r"获得\s*(\d+)\s*点最大生命")
_LEAVE_HINTS = ("放弃", "离开", "结束", "不读了")

# 离线事件启发式使用的代价阈值。
EVENT_HP_FLOOR = 15
EVENT_MAX_HP_LOSS_RATIO = 0.15
EVENT_COST_WEIGHT = 1.5
EVENT_UPGRADE_WEIGHT = 4.0
EVENT_CURSE_WEIGHT = 8.0


def _first_int(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def _event_score(option: dict, hp: int, max_hp: int) -> Optional[float]:
    """为事件选项评分, 代价过高时返回 None.

    真理石板会逐步扣除最大生命 (3, 6, 12, 24, 45).
    旧策略总选索引 0, 曾因此损失大量生命.
    """
    desc = option.get("description") or ""
    title = option.get("title") or ""
    text = f"{title} {desc}"

    if any(hint in text for hint in _LEAVE_HINTS):
        return 0.0

    max_hp_loss = _first_int(_MAX_HP_LOSS, text)
    hp_loss = _first_int(_HP_LOSS, text) - max_hp_loss
    heal = _first_int(_HEAL_EVENT, text)
    max_hp_gain = _first_int(_MAX_HP_GAIN, text)
    curse = 1 if "诅咒" in text else 0
    upgrades = 2 if "所有" in text and "升级" in text else (1 if "升级" in text else 0)

    # 必须满足的安全条件.
    if hp - hp_loss <= EVENT_HP_FLOOR:
        return None
    if max_hp_loss and max_hp - max_hp_loss < max_hp * 0.5:
        return None
    if max_hp_loss and max_hp_loss > max_hp * EVENT_MAX_HP_LOSS_RATIO:
        return None

    return (
        (heal + max_hp_gain) * 1.5
        + upgrades * EVENT_UPGRADE_WEIGHT
        - (hp_loss + max_hp_loss) * EVENT_COST_WEIGHT
        - curse * EVENT_CURSE_WEIGHT
    )


class BuildFlowTool:
    name = "build_flow"

    def __init__(self, client=None, context: Optional[StrategyContext] = None) -> None:
        # 可选的 LLM 选牌: 启发式难以评价成长型卡牌,
        # 例如完美打击和破灭, 因此此前总是选择铁斩波等普通数值牌.
        self.client = client
        self.context = context if context is not None else StrategyContext()
        self._pending_card_open = False
        self._shop = ShopDecision(self.context, self._choose, tool=self.name)
        self._location = None

    def decide(self, request: DecisionRequest) -> Decision:
        location = (request.state.run.act, request.state.run.floor)
        if location != self._location:
            self._pending_card_open = False
            self._shop.reset()
            self._location = location
        if request.scene == "card_reward":
            return self._card_reward(request)
        if request.scene == "rewards":
            return self._rewards(request)
        if request.scene == "event":
            return self._event(request)
        if request.scene in ("rest", "rest_site"):
            return self._rest(request)
        if request.scene == "treasure":
            return self._treasure(request)
        if request.scene == "shop":
            return self._shop.decide(request)
        if request.scene == "bundle_select":
            return self._bundle(request)
        if request.scene == "relic_select":
            return self._relic_select(request)
        return Decision(action=None, reason=f"未实现的界面: {request.scene}", source=Source.NONE, tool=self.name)

    def _choose(self, request: DecisionRequest, task: str, candidates: list,
                notes: dict | None = None) -> Optional[Decision]:
        # 离线运行没有模型, 走启发式; 有模型时失败只重试, 不退回启发式.
        if self.client is None:
            return None
        decision = choose(self.client, request.state, self.context,
                          task=task, candidates=candidates, tool=self.name,
                          attempts=LLM_ATTEMPTS, notes=notes)
        if decision is None:
            return no_decision(self.name, f"模型连续 {LLM_ATTEMPTS} 次未给出有效选择")
        return decision

    def _card_reward(self, request: DecisionRequest) -> Decision:
        cards = request.state.card_reward
        if not cards:
            return Decision(action=SkipCardReward(), reason="没有可选卡", source=Source.HEURISTIC, tool=self.name)

        candidates = [(c.index, SelectCardReward(c.index), asdict(c)) for c in cards]
        candidates.append((-1, SkipCardReward(), {"说明": "跳过卡牌奖励"}))
        decision = self._choose(
            request,
            "选择一张奖励牌或跳过。按主线判断：这张牌服务哪条主线、补的是哪个缺口"
            "（引擎/收益件/格挡/过牌）？"
            "先看 deck_report.combos_missing：能补齐\"只差一半\"的两件套时优先拿；"
            "combos_ready 已成立时只拿补强，不要稀释。"
            "需要前置（例如全身撞击需要壁垒、欺凌需要易伤来源）而前置还没有时，先跳过。"
            "对策卡每幕最多 1~2 张，针对本幕实际威胁，不提前囤。答不上来就跳过。",
            candidates)
        if decision is not None:
            self._pending_card_open = True
            return decision

        best = max(cards, key=lambda c: (card_score(c), -c.index))
        score = card_score(best)
        if score < SKIP_THRESHOLD:
            self._pending_card_open = True
            return Decision(action=SkipCardReward(), reason=f"最高分 {score:.1f} 太低", source=Source.HEURISTIC, tool=self.name)
        self._pending_card_open = True
        return Decision(action=SelectCardReward(best.index), reason=f"选 {best.name}（{score:.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

    def _event(self, request: DecisionRequest) -> Decision:
        """选择收益与代价更合适的选项, 避免不断付出生命.

        真理石板以 3/6/12/24/45 点最大生命换取升级,
        旧策略始终选索引 0, 曾将最大生命降至 1.
        """
        event = request.state.raw.get("event") or {}
        if event.get("in_dialogue"):
            return Decision(action=AdvanceDialogue(), reason="推进对话",
                            source=Source.SCRIPT, tool=self.name)
        options = [o for o in (event.get("options") or [])
                   if isinstance(o, dict) and not o.get("is_locked")]
        if not options:
            # 事件换页时会重建按钮, 暂时没有选项属于过渡状态.
            return WAIT

        decision = self._choose(request, f"选择事件选项：{event.get('event_name', '')}。结合构筑收益和生命、金币等代价。",
                                [(int(o.get("index", 0)), ChooseEventOption(int(o.get("index", 0))), o)
                                 for o in options])
        if decision is not None:
            return decision

        player = request.state.player
        scored: list[tuple[float, int, dict]] = []
        for option in options:
            score = _event_score(option, player.hp, player.max_hp)
            if score is None:
                continue
            scored.append((score, int(option.get("index", 0)), option))

        name = event.get("event_name") or "事件"
        if not scored:
            leave = next(
                (o for o in options
                 if any(h in f"{o.get('title', '')} {o.get('description', '')}" for h in _LEAVE_HINTS)),
                options[-1],
            )
            return Decision(action=ChooseEventOption(int(leave.get("index", 0))),
                            reason=f"{name}: 都太危险，离开（{leave.get('title', '')}）",
                            source=Source.HEURISTIC, tool=self.name)

        score, index, pick = max(scored, key=lambda item: (item[0], -item[1]))
        return Decision(action=ChooseEventOption(index),
                        reason=f"{name}: {pick.get('title', '')}（{score:+.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

    def _rest(self, request: DecisionRequest) -> Decision:
        """篝火: 血量不足时治疗, 否则升级; 同时处理 proceed."""
        site = request.state.raw.get("rest_site") or {}
        if site.get("can_proceed"):
            return Decision(action=Proceed(), reason="离开篝火", source=Source.SCRIPT, tool=self.name)

        options = [o for o in (site.get("options") or [])
                   if isinstance(o, dict) and o.get("is_enabled")]
        if not options:
            return WAIT

        decision = self._choose(
            request,
            "选择篝火行动。默认选择锻造（升级）: 升级是整局永久收益, 回血只解决眼前。"
            "只有下列情况才休息: 血量低于三成; 下一节点就是 Boss 且血量不足以撑过; "
            "牌组已经没有值得升级的牌。回血次数不要超过本幕篝火总数的一半, "
            "升级优先升级后质变的牌（增加段数、降低费用、显著提升易伤层数或格挡数值）, "
            "不要升级基础打击或防御。",
            [(int(o.get("index", 0)), RestOption(int(o.get("index", 0))), o)
             for o in options])
        if decision is not None:
            return decision

        # 提示名称会随语言变化, id 保持稳定.
        # 离线时同样遵循攻略口径: 回血只在血量很低时才算划算.
        want = "HEAL" if request.state.player.hp_ratio < 0.35 else "SMITH"
        pick = next((o for o in options if str(o.get("id")).upper() == want), options[0])
        reason = f"{pick.get('name', '')}: {pick.get('description', '')}"[:60]
        return Decision(action=RestOption(int(pick.get("index", 0))), reason=reason,
                        source=Source.HEURISTIC, tool=self.name)

    def _treasure(self, request: DecisionRequest) -> Decision:
        """宝箱: 先领取遗物再离开, 遗物在动画结束后才出现."""
        treasure = request.state.raw.get("treasure") or {}
        relics = [r for r in (treasure.get("relics") or []) if isinstance(r, dict)]
        if relics:
            candidates = [(int(r.get("index", 0)), ClaimTreasureRelic(int(r.get("index", 0))), r)
                          for r in relics]
            if treasure.get("can_proceed"):
                candidates.append((-1, Proceed(), {"说明": "放弃剩余遗物并离开"}))
            decision = self._choose(request, "选择宝箱遗物，结合效果及当前构筑判断。", candidates)
            if decision is not None:
                return decision
            pick = relics[0]
            return Decision(action=ClaimTreasureRelic(int(pick.get("index", 0))),
                            reason=f"拿取 {pick.get('name', '遗物')}",
                            source=Source.SCRIPT, tool=self.name)
        if treasure.get("can_proceed"):
            return Decision(action=Proceed(), reason="离开宝箱", source=Source.SCRIPT, tool=self.name)
        return WAIT

    def _bundle(self, request: DecisionRequest) -> Decision:
        """选择总价值最高的卡牌包, 然后确认."""
        bundle = request.state.raw.get("bundle_select") or {}
        if bundle.get("can_confirm") or bundle.get("preview_showing"):
            return Decision(action=ConfirmBundleSelection(), reason="确认卡牌包",
                            source=Source.SCRIPT, tool=self.name)

        bundles = [b for b in (bundle.get("bundles") or []) if isinstance(b, dict)]
        if not bundles:
            return WAIT

        decision = self._choose(request, "选择与当前构筑配合的卡牌包。",
                                [(int(b.get("index", 0)), SelectBundle(int(b.get("index", 0))), b)
                                 for b in bundles])
        if decision is not None:
            return decision

        def value(item: dict) -> float:
            return sum(_card_dict_value(c) for c in (item.get("cards") or []) if isinstance(c, dict))

        best = max(bundles, key=value)
        return Decision(action=SelectBundle(int(best.get("index", 0))),
                        reason=f"选价值最高的一包（{value(best):.1f}）",
                        source=Source.HEURISTIC, tool=self.name)

    def _relic_select(self, request: DecisionRequest) -> Decision:
        raw = request.state.raw.get("relic_select") or {}
        relics = [r for r in raw.get("relics") or [] if isinstance(r, dict)]
        candidates = [(int(r.get("index", 0)), SelectRelic(int(r.get("index", 0))), r) for r in relics]
        if raw.get("can_skip"):
            candidates.append((-1, SkipRelicSelection(), {"说明": "跳过遗物选择"}))
        decision = self._choose(request, "选择遗物或在允许时跳过，考虑构筑配合与负面效果。", candidates)
        if decision is not None:
            return decision
        if relics:
            return Decision(action=SelectRelic(int(relics[0].get("index", 0))),
                            reason="无有效模型选择，取第一个遗物", source=Source.HEURISTIC, tool=self.name)
        if raw.get("can_skip"):
            return Decision(action=SkipRelicSelection(), reason="没有遗物候选，跳过",
                            source=Source.HEURISTIC, tool=self.name)
        return WAIT

    def _rewards(self, request: DecisionRequest) -> Decision:
        raw = request.state.raw.get("rewards") or {}
        items = request.state.rewards
        can_proceed = bool(raw.get("can_proceed"))
        player = request.state.player
        potions_full = len(player.potions) >= player.max_potion_slots

        # 金币不涉及构筑取舍，先领取以减少不必要的模型调用。
        gold = next((r for r in items if r.type == "gold"), None)
        if gold is not None:
            return Decision(action=ClaimReward(gold.index), reason="领取金币",
                            source=Source.SCRIPT, tool=self.name)

        raw_items = {r.get("index"): r for r in raw.get("items") or [] if isinstance(r, dict)}
        candidates = [(r.index, ClaimReward(r.index), raw_items.get(r.index, asdict(r)))
                      for r in items if not (r.type == "potion" and potions_full)
                      and not (r.type == "card" and self._pending_card_open)]
        if can_proceed:
            candidates.append((-1, Proceed(), {"说明": "放弃剩余奖励并离开"}))
        if not candidates:
            # 奖励列表已清空且继续不可用: 这是界面切换中的过渡帧, 等待下一帧,
            # 不能当成"模型没给决策"而中止整局.
            return WAIT
        incoming = [raw_items.get(r.index, asdict(r)) for r in items if r.type == "potion"]
        if potions_full and incoming and player.max_potion_slots > 0:
            for potion in player.potions:
                candidates.append((-2 - potion.slot, DiscardPotion(potion.slot),
                                   {"说明": "先丢弃现有药水；下一步重读奖励索引再领取",
                                    "discard": asdict(potion), "incoming_potions": incoming}))
        decision = self._choose(request, "领取奖励或离开；药水槽满时可先丢弃旧药水，为更合适的奖励腾出位置。", candidates)
        if decision is not None:
            return decision

        # 药水槽位已满时无法领取药水奖励, 重复领取会导致卡住,
        # 因此将其视为不可领取并继续前进.
        claimable = [
            r for r in items
            if r.type != "card" and not (r.type == "potion" and potions_full)
        ]
        if claimable:
            return Decision(action=ClaimReward(claimable[0].index), reason=f"领取 {claimable[0].type}",
                            source=Source.SCRIPT, tool=self.name)

        card_items = [r for r in items if r.type == "card"]
        if card_items:
            if self._pending_card_open and can_proceed:
                self._pending_card_open = False
                return Decision(action=Proceed(), reason="卡牌奖励已处理，离开",
                                source=Source.SCRIPT, tool=self.name)
            return Decision(action=ClaimReward(card_items[0].index), reason="打开选牌",
                            source=Source.SCRIPT, tool=self.name)

        if can_proceed:
            return Decision(action=Proceed(), reason="离开奖励界面", source=Source.SCRIPT, tool=self.name)
        return Decision(action=None, reason="奖励界面无可执行动作", source=Source.NONE, tool=self.name)
