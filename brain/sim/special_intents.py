"""已注册的特殊意图: 参数校验, 只读预览和实际执行各自独立."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .engine import DeckCard, Fight, Monster


INFECTION = DeckCard(
    name="感染", id="INFECTION", cost=0, damage=3, target="None", playable=False,
    end_turn_damage=3,
    description="不能被打出. 在你的回合结束时, 如果这张牌在你的手牌中, 你受到3点伤害.",
)

WOUND = DeckCard(
    name="伤口", id="WOUND", cost=0, target="None", playable=False,
    description="不能被打出.",
)

# 呼唤: 第一幕 Boss 灵魂异鱼用"策略"意图塞进手牌的状态牌.
# 游戏内文本: "在你的回合结束时，如果这张牌在你的手牌中，你失去6点生命。"
BECKON = DeckCard(
    name="呼唤", id="BECKON", cost=1, target="None", playable=True, end_turn_damage=6,
    description="在你的回合结束时，如果这张牌在你的手牌中，你失去6点生命。",
)


def validate_beckon(params: Any) -> None:
    """只接受明确的数量和目标牌堆."""
    if not isinstance(params, dict) or params.keys() != {"count", "pile"}:
        raise ValueError("add_beckon.params 必须且只能包含 count 和 pile")
    if type(params["count"]) is not int or params["count"] < 1:
        raise ValueError("add_beckon.count 必须是大于等于 1 的整数")
    if params["pile"] not in ("draw_pile", "discard_pile"):
        raise ValueError("add_beckon.pile 只支持 draw_pile 或 discard_pile")


def preview_beckon(fight: Fight, monster: Monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    pile = "抽牌堆" if params["pile"] == "draw_pile" else "弃牌堆"
    return [{"type": "StatusCard", "label": str(params["count"]),
             "description": f"将 {params['count']} 张呼唤加入{pile}."}]


def execute_beckon(fight: Fight, monster: Monster, params: dict[str, Any]) -> None:
    pile = fight.draw_pile if params["pile"] == "draw_pile" else fight.discard
    for _ in range(params["count"]):
        card = replace(BECKON)
        if params["pile"] == "draw_pile":
            pile.insert(fight.rng.randrange(len(pile) + 1), card)
        else:
            pile.append(card)


def validate_infection(params: Any) -> None:
    """只接受明确的数量和目标牌堆, 在加载文件时发现配置错误."""
    if not isinstance(params, dict) or params.keys() != {"count", "pile"}:
        raise ValueError("add_infection.params 必须且只能包含 count 和 pile")
    if type(params["count"]) is not int or params["count"] < 1:
        raise ValueError("add_infection.count 必须是大于等于 1 的整数")
    if params["pile"] not in ("draw_pile", "discard_pile"):
        raise ValueError("add_infection.pile 只支持 draw_pile 或 discard_pile")


def preview_infection(fight: Fight, monster: Monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    """返回意图信息, 不修改状态或消耗随机数."""
    pile = "抽牌堆" if params["pile"] == "draw_pile" else "弃牌堆"
    return [{"type": "StatusCard", "label": str(params["count"]),
             "description": f"将 {params['count']} 张感染加入{pile}."}]


def execute_infection(fight: Fight, monster: Monster, params: dict[str, Any]) -> None:
    """向弃牌堆追加感染, 或使用本场随机源将感染随机插入抽牌堆."""
    pile = fight.draw_pile if params["pile"] == "draw_pile" else fight.discard
    for _ in range(params["count"]):
        card = replace(INFECTION)
        if params["pile"] == "draw_pile":
            pile.insert(fight.rng.randrange(len(pile) + 1), card)
        else:
            pile.append(card)


def _validate_gain_plow(params: Any) -> None:
    """跺地: 获得固定层数的横冲直撞, 同时决定击晕阈值."""
    if not isinstance(params, dict) or params.keys() != {"amount"}:
        raise ValueError("gain_plow.params 必须且只能包含 amount")
    if type(params["amount"]) is not int or params["amount"] < 1:
        raise ValueError("gain_plow.amount 必须是大于等于 1 的整数")


def _preview_gain_plow(fight: Fight, monster: Monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "Buff", "label": "", "title": "强化",
             "description": f"获得 {params['amount']} 层横冲直撞。"}]


def _execute_gain_plow(fight: Fight, monster: Monster, params: dict[str, Any]) -> None:
    amount = int(params["amount"])
    monster.powers["PLOW_POWER"] = amount
    # 仪式兽: 血量第一次降到该层数(含)以下且受到伤害时被击晕.
    monster.stun_below_hp = amount


def _validate_apply_ringing(params: Any) -> None:
    if not isinstance(params, dict) or params.keys() != {"count"}:
        raise ValueError("apply_ringing.params 必须且只能包含 count")
    if type(params["count"]) is not int or params["count"] < 1:
        raise ValueError("apply_ringing.count 必须是大于等于 1 的整数")


def _preview_apply_ringing(fight: Fight, monster: Monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "Debuff", "label": "", "title": "策略",
             "description": f"给玩家施加 {params['count']} 层昏眩 (本回合只能打出1张牌)。"}]


def _execute_apply_ringing(fight: Fight, monster: Monster, params: dict[str, Any]) -> None:
    fight.ringing_pending = int(params["count"])


def _validate_sunder(params: Any) -> None:
    """墨影幻灵的肢解: 造成伤害, 并往弃牌堆塞伤口."""
    if not isinstance(params, dict) or params.keys() != {"damage", "wound"}:
        raise ValueError("sunder.params 必须且只能包含 damage 和 wound")
    if type(params["damage"]) is not int or params["damage"] < 1:
        raise ValueError("sunder.damage 必须是大于等于 1 的整数")
    if type(params["wound"]) is not int or params["wound"] < 0:
        raise ValueError("sunder.wound 必须是大于等于 0 的整数")


def _preview_sunder(fight: Fight, monster: Monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "Attack", "damage": max(0, params["damage"] + monster.strength), "hits": 1},
        {"type": "StatusCard", "label": str(params["wound"]),
         "description": f"将 {params['wound']} 张伤口加入你的弃牌堆。"},
    ]


def _execute_sunder(fight: Fight, monster: Monster, params: dict[str, Any]) -> None:
    damage = max(0, int(params["damage"]) + monster.strength)
    if monster.weak:
        damage = int(damage * 0.75)
    fight._take_damage(damage)
    for _ in range(int(params["wound"])):
        fight.discard.append(replace(WOUND))




# --- 女王: 拘束 (Chains of Binding) ---
# 游戏内文本: 每回合最先抽到的 3 张牌被拘束, 每回合只能打出其中 1 张, 回合结束解除.
CHAINS_PER_TURN = 3


def _validate_chains(params: Any) -> None:
    if params not in (None, {}):
        raise ValueError("apply_chains 不接受参数")


def _preview_chains(fight, monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "Debuff", "label": "拘束",
             "description": f"每回合最先抽到的 {CHAINS_PER_TURN} 张牌只能打出 1 张。"}]


def _execute_chains(fight, monster, params: dict[str, Any]) -> None:
    fight.chained_draw = CHAINS_PER_TURN


# --- 女王: 你是我的了 (99 层脆弱 / 虚弱 / 易伤) ---
QUEEN_DEBUFF_LAYERS = 99


def _validate_queen_debuffs(params: Any) -> None:
    if params not in (None, {}):
        raise ValueError("queen_debuffs 不接受参数")


def _preview_queen_debuffs(fight, monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "Debuff", "label": "你是我的了",
             "description": f"获得 {QUEEN_DEBUFF_LAYERS} 层脆弱 / 虚弱 / 易伤。"}]


def _execute_queen_debuffs(fight, monster, params: dict[str, Any]) -> None:
    fight.player_frail = QUEEN_DEBUFF_LAYERS
    fight.player_weak = QUEEN_DEBUFF_LAYERS
    fight.player_vulnerable = QUEEN_DEBUFF_LAYERS


# --- 女王: 为我尽力燃烧吧 (聚合体 +1 力量, 自己 +20 格挡) ---
QUEEN_SUPPORT_BLOCK = 20


def _validate_queen_support(params: Any) -> None:
    if params not in (None, {}):
        raise ValueError("queen_support 不接受参数")


def _preview_queen_support(fight, monster, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "Buff", "label": "为我尽力燃烧吧",
             "description": f"聚合体 +1 力量, 女王获得 {QUEEN_SUPPORT_BLOCK} 点格挡。"}]


def _execute_queen_support(fight, monster, params: dict[str, Any]) -> None:
    monster.block += QUEEN_SUPPORT_BLOCK
    for other in fight.monsters:
        if other.alive and other is not monster:
            other.strength += 1


CUSTOM_INTENTS = {
    "apply_chains": {
        "validate": _validate_chains,
        "preview": _preview_chains,
        "execute": _execute_chains,
    },
    "queen_debuffs": {
        "validate": _validate_queen_debuffs,
        "preview": _preview_queen_debuffs,
        "execute": _execute_queen_debuffs,
    },
    "queen_support": {
        "validate": _validate_queen_support,
        "preview": _preview_queen_support,
        "execute": _execute_queen_support,
    },
    "add_infection": {
        "validate": validate_infection,
        "preview": preview_infection,
        "execute": execute_infection,
    },
    "add_beckon": {
        "validate": validate_beckon,
        "preview": preview_beckon,
        "execute": execute_beckon,
    },
    "gain_plow": {
        "validate": _validate_gain_plow,
        "preview": _preview_gain_plow,
        "execute": _execute_gain_plow,
    },
    "apply_ringing": {
        "validate": _validate_apply_ringing,
        "preview": _preview_apply_ringing,
        "execute": _execute_apply_ringing,
    },
    "sunder": {
        "validate": _validate_sunder,
        "preview": _preview_sunder,
        "execute": _execute_sunder,
    },
}
