"""类型化领域模型, 结构参考一代 Agent 的 `spirecomm/spire/`.

统一解析原始 JSON, 避免各策略分别判断当前界面, 卡牌索引和攻击意图,
造成细微差异. 解析集中在这里完成, 策略使用类型化对象.

沿用一代项目的两条约定:

* 解析始终有结果: 忽略未知字段, 缺失字段使用默认值.
  游戏补丁最多影响个别字段, 不应使模型崩溃.
* 在 ``GameState.raw`` 中保留原始字典, 供需要尚未建模字段的策略使用.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .effects import incoming_damage, parse_card


class Screen(str, Enum):
    """将桥接的 ``state_type`` 值统一表示为枚举."""

    MENU = "menu"
    MAP = "map"
    COMBAT = "monster"
    ELITE = "elite"
    BOSS = "boss"
    REWARDS = "rewards"
    CARD_REWARD = "card_reward"
    EVENT = "event"
    SHOP = "shop"
    REST = "rest"
    REST_SITE = "rest_site"
    TREASURE = "treasure"
    RELIC_SELECT = "relic_select"
    HAND_SELECT = "hand_select"
    CARD_SELECT = "card_select"
    BUNDLE_SELECT = "bundle_select"
    GAME_OVER = "game_over"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "Screen":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list:
    return value if isinstance(value, list) else []


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _s(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


@dataclass(frozen=True)
class Card:
    index: int = 0
    id: str = ""
    name: str = ""
    cost: int = 1
    type: str = ""
    target_type: str = ""
    can_play: bool = True
    damage: int = 0
    hits: int = 1
    block: int = 0
    vulnerable: int = 0
    weak: int = 0
    draw: int = 0
    energy: int = 0
    # 能力牌提供的永久成长 (如燃烧的 +2 力量), 只在打出时生效.
    strength: int = 0
    dexterity: int = 0
    # 覆甲: 回合结束时按层数获得格挡.
    plated: int = 0
    # 以生命为费用 (放血: 失去 3 点生命).
    hp_cost: int = 0
    # 每回合开始获得的能量 (薪火之源); 价值在后续回合.
    per_turn_energy: int = 0
    # 后续回合的格挡 (坚韧之环): 持续回合数与每回合格挡.
    future_block_turns: int = 0
    future_block_per_turn: int = 0
    # 随机消耗手牌的张数 (未升级的坚毅): 可能烧掉核心牌.
    random_exhaust: int = 0
    # 每回合开始的格挡与其生命代价 (绯红披风).
    per_turn_block: int = 0
    per_turn_hp_cost: int = 0
    # 全身撞击: 伤害等于打出时的格挡.
    body_slam: bool = False
    # 欺凌: 目标每层易伤额外伤害; 灰烬打击: 消耗堆每张额外伤害.
    per_vulnerable: int = 0
    per_exhaust: int = 0
    # 打出后自身消耗 (战栗/契约终章), 计入灰烬打击的成长.
    exhaust: bool = False
    # 目标有易伤时的命中段数 (拆卸: 攻击两次).
    hits_if_vulnerable: int = 0
    # 游戏自己算好的单次命中伤害, 按敌人 entity_id 给出 (含力量/易伤/附魔/遗物).
    damage_by_target: dict[str, int] = field(default_factory=dict)
    rarity: str = ""
    upgraded: bool = False
    selected: bool = False
    # 桥接提供的升级数值. None 表示数值未知,
    # 模拟器应使用启发式估计.
    damage_up: Optional[int] = None
    block_up: Optional[int] = None
    # 应急按钮: "接下来的 N 回合内无法再从卡牌中获得格挡"
    no_block_turns: int = 0
    description: str = ""

    @classmethod
    def parse(cls, raw: Any, index: int = 0) -> "Card":
        d = _d(raw)
        parsed = parse_card(d)
        return cls(
            index=_i(d.get("index"), index),
            id=_s(d.get("id")),
            name=_s(d.get("name")),
            cost=parsed["cost"],
            type=parsed["type"],
            target_type=parsed["target_type"],
            can_play=parsed["can_play"] and parsed["star_cost"] <= 0,
            damage=parsed["damage"],
            hits=parsed["hits"],
            block=parsed["block"],
            vulnerable=parsed["vulnerable"],
            weak=parsed["weak"],
            draw=parsed["draw"],
            energy=parsed["energy"],
            strength=parsed.get("strength", 0),
            dexterity=parsed.get("dexterity", 0),
            plated=parsed.get("plated", 0),
            hp_cost=parsed.get("hp_cost", 0),
            per_turn_energy=parsed.get("per_turn_energy", 0),
            future_block_turns=parsed.get("future_block_turns", 0),
            future_block_per_turn=parsed.get("future_block_per_turn", 0),
            random_exhaust=parsed.get("random_exhaust", 0),
            per_turn_block=parsed.get("per_turn_block", 0),
            per_turn_hp_cost=parsed.get("per_turn_hp_cost", 0),
            body_slam=parsed.get("body_slam", False),
            per_vulnerable=parsed.get("per_vulnerable", 0),
            per_exhaust=parsed.get("per_exhaust", 0),
            exhaust=parsed.get("exhaust", False),
            hits_if_vulnerable=parsed.get("hits_if_vulnerable", 0),
            damage_by_target=dict(parsed.get("damage_by_target") or {}),
            rarity=_s(d.get("rarity")),
            upgraded=bool(d.get("is_upgraded")),
            selected=bool(d.get("is_selected")),
            damage_up=d.get("damage_upgraded") if isinstance(d.get("damage_upgraded"), int) else None,
            block_up=d.get("block_upgraded") if isinstance(d.get("block_upgraded"), int) else None,
            no_block_turns=parsed.get("no_block_turns", 0),
            description=_s(d.get("description")),
        )

    @property
    def needs_target(self) -> bool:
        if self.target_type:
            return self.target_type.lower().endswith("enemy")
        # 旧版或不完整状态可能缺少 target_type; 此时将伤害牌视为需要目标.
        return self.damage > 0


@dataclass(frozen=True)
class Intent:
    type: str = ""
    damage: int = 0
    hits: int = 1
    label: str = ""

    @classmethod
    def parse(cls, raw: Any) -> "Intent":
        d = _d(raw)
        # 带补丁的 Mod 输出数值, 旧版只有文本.
        # 数值字段是基础伤害, 标签/描述才是结算后的值 (见 effects.resolved_attack),
        # 因此攻击意图统一走解析, 避免低估来袭伤害.
        from .effects import resolved_attack

        kind = _s(d.get("type"))
        if "attack" in kind.lower():
            damage, hits = resolved_attack(d)
            return cls(kind, damage, hits, _s(d.get("label")))
        if d.get("damage") is not None:
            return cls(kind, _i(d.get("damage")), max(1, _i(d.get("hits"), 1)), _s(d.get("label")))
        from .effects import parse_intent

        damage, hits = parse_intent(d)
        return cls(kind, damage, hits, _s(d.get("label")))

    @property
    def is_attack(self) -> bool:
        return "attack" in self.type.lower() and self.damage > 0


@dataclass(frozen=True)
class Enemy:
    entity_id: str = ""
    name: str = ""
    hp: int = 0
    max_hp: int = 0
    block: int = 0
    intents: tuple[Intent, ...] = ()
    powers: dict[str, int] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any) -> "Enemy":
        d = _d(raw)
        return cls(
            entity_id=_s(d.get("entity_id")),
            name=_s(d.get("name")),
            hp=_i(d.get("hp")),
            max_hp=_i(d.get("max_hp"), _i(d.get("hp"))),
            block=_i(d.get("block")),
            intents=tuple(Intent.parse(x) for x in _l(d.get("intents"))),
            # 使用稳定 id 而非本地化名称作为键,
            # 使模拟器在游戏切换语言后仍可工作.
            powers={
                _s(p.get("id") or p.get("name")): _i(p.get("amount"))
                for p in _l(d.get("status"))
                if isinstance(p, dict)
            },
        )

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def incoming(self) -> int:
        return sum(i.damage * i.hits for i in self.intents if i.is_attack)


@dataclass(frozen=True)
class Potion:
    slot: int = 0
    id: str = ""
    name: str = ""
    description: str = ""
    target_type: str = ""
    usable: bool = True

    @classmethod
    def parse(cls, raw: Any, index: int = 0) -> "Potion":
        d = _d(raw)
        return cls(
            slot=_i(d.get("slot"), index),
            id=_s(d.get("id")),
            name=_s(d.get("name")),
            description=_s(d.get("description")),
            target_type=_s(d.get("target_type")),
            usable=bool(d.get("can_use_in_combat", True)),
        )

    @property
    def needs_enemy_target(self) -> bool:
        return self.target_type.lower().endswith("enemy")


@dataclass(frozen=True)
class Player:
    character: str = ""
    hp: int = 0
    max_hp: int = 1
    block: int = 0
    energy: int = 0
    max_energy: int = 3
    gold: int = 0
    max_potion_slots: int = 3
    relics: tuple[str, ...] = ()
    potions: tuple[Potion, ...] = ()
    deck: tuple[Card, ...] = ()
    powers: dict[str, int] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any) -> "Player":
        d = _d(raw)
        return cls(
            character=_s(d.get("character")),
            hp=_i(d.get("hp")),
            max_hp=_i(d.get("max_hp"), 1),
            block=_i(d.get("block")),
            energy=_i(d.get("energy")),
            max_energy=_i(d.get("max_energy"), 3),
            gold=_i(d.get("gold")),
            max_potion_slots=_i(d.get("max_potion_slots"), 3),
            relics=tuple(_s(r.get("name") or r.get("id")) for r in _l(d.get("relics")) if isinstance(r, dict)),
            potions=tuple(Potion.parse(p, i) for i, p in enumerate(_l(d.get("potions"))) if isinstance(p, dict)),
            deck=tuple(Card.parse(c, i) for i, c in enumerate(_l(d.get("deck")))),
            powers={
                _s(p.get("id") or p.get("name")): _i(p.get("amount"))
                for p in _l(d.get("status"))
                if isinstance(p, dict)
            },
        )

    @property
    def hp_ratio(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0.0


@dataclass(frozen=True)
class Battle:
    round: int = 1
    turn: str = ""
    ready: bool = False
    enemies: tuple[Enemy, ...] = ()
    hand: tuple[Card, ...] = ()
    draw_count: int = 0
    discard_count: int = 0
    exhaust_count: int = 0

    @classmethod
    def parse(cls, raw: Any, player_raw: Any) -> "Battle":
        d = _d(raw)
        p = _d(player_raw)
        return cls(
            round=_i(d.get("round"), 1),
            turn=_s(d.get("turn")),
            ready=bool(d.get("ready_for_action")),
            enemies=tuple(Enemy.parse(e) for e in _l(d.get("enemies"))),
            hand=tuple(Card.parse(c, i) for i, c in enumerate(_l(p.get("hand")))),
            draw_count=_i(p.get("draw_pile_count")),
            discard_count=_i(p.get("discard_pile_count")),
            exhaust_count=_i(p.get("exhaust_pile_count")),
        )

    @property
    def alive_enemies(self) -> tuple[Enemy, ...]:
        return tuple(e for e in self.enemies if e.alive)

    @property
    def incoming(self) -> int:
        return sum(e.incoming for e in self.alive_enemies)

    @property
    def player_turn(self) -> bool:
        return self.turn.lower() == "player"

    @property
    def can_act(self) -> bool:
        return self.ready and self.player_turn


@dataclass(frozen=True)
class MapOption:
    index: int = 0
    type: str = ""
    leads_to: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: Any, fallback: int = 0) -> "MapOption":
        d = _d(raw)
        return cls(
            index=_i(d.get("index"), fallback),
            type=_s(d.get("type")),
            leads_to=tuple(_s(x.get("type")) for x in _l(d.get("leads_to")) if isinstance(x, dict)),
        )


@dataclass(frozen=True)
class MapState:
    options: tuple[MapOption, ...] = ()
    boss: str = ""
    visited: int = 0

    @classmethod
    def parse(cls, raw: Any) -> "MapState":
        d = _d(raw)
        return cls(
            options=tuple(MapOption.parse(x, i) for i, x in enumerate(_l(d.get("next_options")))),
            boss=_s(_d(d.get("boss")).get("name")),
            visited=len(_l(d.get("visited"))),
        )


@dataclass(frozen=True)
class RewardItem:
    index: int = 0
    type: str = ""
    description: str = ""
    gold: int = 0
    potion_id: str = ""

    @classmethod
    def parse(cls, raw: Any) -> "RewardItem":
        d = _d(raw)
        return cls(
            index=_i(d.get("index")),
            type=_s(d.get("type")),
            description=_s(d.get("description")),
            gold=_i(d.get("gold_amount")),
            potion_id=_s(d.get("potion_id")),
        )


@dataclass(frozen=True)
class HandSelect:
    """选牌弹窗: 弃牌 / 消耗 / 升级 / 选择卡牌."""

    mode: str = ""
    prompt: str = ""
    cards: tuple[Card, ...] = ()
    can_confirm: bool = False
    preview_showing: bool = False

    @classmethod
    def parse(cls, raw: Any) -> "HandSelect":
        d = _d(raw)
        return cls(
            mode=_s(d.get("mode") or d.get("screen_type")),
            prompt=_s(d.get("prompt")),
            cards=tuple(Card.parse(c, i) for i, c in enumerate(_l(d.get("cards")))),
            can_confirm=bool(d.get("can_confirm")),
            preview_showing=bool(d.get("preview_showing")),
        )


@dataclass(frozen=True)
class RunInfo:
    act: int = 1
    floor: int = 0
    ascension: int = 0

    @classmethod
    def parse(cls, raw: Any) -> "RunInfo":
        d = _d(raw)
        return cls(_i(d.get("act"), 1), _i(d.get("floor")), _i(d.get("ascension")))


@dataclass(frozen=True)
class GameState:
    screen: Screen = Screen.UNKNOWN
    player: Player = field(default_factory=Player)
    battle: Optional[Battle] = None
    map: Optional[MapState] = None
    rewards: tuple[RewardItem, ...] = ()
    card_reward: tuple[Card, ...] = ()
    hand_select: Optional[HandSelect] = None
    run: RunInfo = field(default_factory=RunInfo)
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def parse(cls, raw: Any) -> "GameState":
        d = _d(raw)
        player_raw = d.get("player")
        battle_raw = d.get("battle")
        return cls(
            screen=Screen.parse(d.get("state_type")),
            player=Player.parse(player_raw),
            battle=Battle.parse(battle_raw, player_raw) if isinstance(battle_raw, dict) else None,
            map=MapState.parse(d.get("map")) if isinstance(d.get("map"), dict) else None,
            rewards=tuple(RewardItem.parse(r) for r in _l(_d(d.get("rewards")).get("items"))),
            card_reward=tuple(Card.parse(c, i) for i, c in enumerate(_l(_d(d.get("card_reward")).get("cards")))),
            hand_select=(
                HandSelect.parse(d.get("hand_select"))
                if isinstance(d.get("hand_select"), dict)
                else HandSelect.parse(d.get("card_select"))
                if isinstance(d.get("card_select"), dict)
                else None
            ),
            run=RunInfo.parse(d.get("run")),
            message=_s(d.get("message")),
            raw=d,
        )

    # -- 供 Agent 使用的便捷判断 --
    @property
    def in_combat(self) -> bool:
        return self.screen in (Screen.COMBAT, Screen.ELITE, Screen.BOSS) and self.battle is not None

    @property
    def is_over(self) -> bool:
        return self.screen is Screen.GAME_OVER
