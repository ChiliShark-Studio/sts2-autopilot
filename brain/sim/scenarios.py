"""独立竞技场使用的初始卡组和简单遭遇.

这些场景刻意保持简单, 数值不代表真实游戏.
它们用于让两个策略版本在相同战斗中比较, 后续可随策略完善增加场景.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import DeckCard, Intent, Monster

STRIKE = DeckCard("打击", cost=1, damage=6, target="AnyEnemy")
DEFEND = DeckCard("防御", cost=1, block=5, target="Self")
NEUTRALIZE = DeckCard("中和", cost=0, damage=3, weak=1, target="AnyEnemy")
SURVIVOR = DeckCard("生存者", cost=1, block=8, target="Self")
BASH = DeckCard("痛击", cost=2, damage=8, vulnerable=2, target="AnyEnemy")


def starter(character: str = "silent") -> list[DeckCard]:
    character = character.lower()
    if character in ("ironclad", "铁甲战士"):
        return [STRIKE] * 5 + [DEFEND] * 4 + [BASH]
    # 静默猎手
    return [STRIKE] * 5 + [DEFEND] * 5 + [NEUTRALIZE, SURVIVOR]


def _nibbit() -> list[Monster]:
    return [Monster("小啃兽", hp=44, max_hp=44, script=[Intent("attack", 12)])]


def _slimes() -> list[Monster]:
    return [
        Monster("树叶史莱姆（小）", hp=11, max_hp=11, script=[Intent("attack", 3)]),
        Monster("树叶史莱姆（中）", hp=34, max_hp=34, script=[Intent("attack", 8)]),
        Monster("树枝史莱姆（小）", hp=10, max_hp=10, script=[Intent("attack", 4)]),
    ]


def _jaw_worm() -> list[Monster]:
    # 交替执行重击和强化, 用于近似前期较强敌人的战斗节奏.
    return [Monster("颚虫", hp=44, max_hp=44, script=[Intent("attack", 11), Intent("buff", strength=3)])]


def _lagavulin() -> list[Monster]:
    return [Monster("拉加维林", hp=109, max_hp=109,
                    script=[Intent("attack", 18), Intent("attack", 18), Intent("attack", 18)])]


def _ceremonial_beast() -> list[Monster]:
    """密林 Boss 仪式兽: 252 血, 两阶段.

    一阶段: 第 1 回合跺地获得 150 层横冲直撞, 之后每回合横冲直撞(18 伤 +2 力量).
    过渡: 血量第一次降到 150 或以下且受到伤害 -> 击晕一回合, 力量清零.
    二阶段: 野兽咆哮(给玩家昏眩) -> 踩踏(15) -> 碾碎(17 伤 +3 力量) 循环.

    数值来源: 游戏内卡牌/状态文本与灰机 wiki、Spire Codex; 非官方导出数据,
    仅用于同种子下的策略对比, 不能当作真实胜率.
    """
    stamp = Intent("custom", handler="gain_plow", params={"amount": 150})
    plow = Intent("attack", damage=18, strength=2)
    cry = Intent("custom", handler="apply_ringing", params={"count": 1})
    stomp = Intent("attack", damage=15)
    crush = Intent("attack", damage=17, strength=3)
    return [Monster(
        "仪式兽", hp=252, max_hp=252,
        # 跺地只在第 1 回合, 之后重复横冲直撞.
        script=[stamp, plow], repeat=True, repeat_from=1,
        entity_id="CEREMONIAL_BEAST_0",
        next_phase_script=[cry, stomp, crush],
    )]


def _vantom() -> list[Monster]:
    """密林 Boss 墨影幻灵: 173 血, 固有滑溜 8.

    行动循环: 墨迹(7) -> 墨水长枪(6x2) -> 肢解(26 并塞伤口) -> 准备(+2 力量).
    滑溜 8: 接下来 8 次失去生命时, 每次只失去 1 点; 多段攻击能更快削掉层数.

    数值来源: Spire Codex (173 HP, 滑溜 8, 招式 7 / 6x2 / 26 / +2 力量);
    属于近似重建, 用于同种子下的策略对比, 不代表真实胜率.
    """
    return [Monster(
        "墨影幻灵", hp=173, max_hp=173,
        script=[
            Intent("attack", damage=7),
            Intent("attack", damage=6, hits=2),
            Intent("custom", handler="sunder", params={"damage": 26, "wound": 1}),
            Intent("buff", strength=2),
        ],
        entity_id="VANTOM_0", slippery=8,
    )]


def _bygone_effigy() -> list[Monster]:
    """密林精英 旧日雕像: 127 血, 固有缓慢.

    行动: 沉睡(开局不行动) -> 苏醒(+10 力量) -> 斩击(13) 循环.
    缓慢: 本回合每打出一张牌, 从攻击牌受到的伤害 +10% (回合结束清零),
    所以攻击牌应当留到最后打.
    数值来源: Spire Codex (127 血, 招式 沉睡/苏醒+10 力量/斩击 13, 固有 Slow).
    """
    return [Monster(
        "旧日雕像", hp=127, max_hp=127,
        script=[
            Intent("sleep"),
            Intent("buff", strength=10),
            Intent("attack", damage=13),
        ],
        repeat=True, repeat_from=2, entity_id="BYGONE_EFFIGY_0", has_slow=True,
    )]


def _byrdonis() -> list[Monster]:
    """密林精英 多尼斯异鸟: 81-84 血, 固有领地 1.

    领地: 自己回合结束时获得 1 点力量 (每回合累积, 所以拖得越久越疼).
    行动循环: 啄击(3x3) -> 飞扑(17).
    数值来源: Spire Codex (81-84 血, Territorial 1, 啄击 3x3 / 飞扑 17).
    """
    return [Monster(
        "多尼斯异鸟", hp=84, max_hp=84,
        script=[Intent("attack", damage=3, hits=3), Intent("attack", damage=17)],
        entity_id="BYRDONIS_0", territorial=1,
    )]


def _skulking_colony() -> list[Monster]:
    """密林精英 鬼祟珊瑚群: 75 血, 固有硬化外壳 20.

    硬化外壳 (HARDENED_SHELL_POWER): 每回合失去的生命值不超过 20 点.
    状态里给的是当回合剩余额度, 每回合重置, 因此单回合输出超过 20 点的部分
    完全浪费——这一条决定了它不能靠爆发速杀, 只能按回合磨.
    行动 (真机录像里出现的三种, 出现频率不同, 此处近似成一个循环):
    攻势 14 / 攻势 9 并 +2 力量 / 攻势 7 两段.
    证据: logs/run-20261114-044556.jsonl 第一幕第 11 层 (75 血, 硬化外壳 20,
    额度 20 -> 12 -> 3 后重置), 非官方导出数据, 仅用于同种子策略对比.
    """
    return [Monster(
        "鬼祟珊瑚群", hp=75, max_hp=75,
        script=[Intent("attack", damage=14),
                Intent("attack", damage=9, strength=2),
                Intent("attack", damage=7, hits=2)],
        entity_id="SKULKING_COLONY_0", hp_loss_cap=20, hp_loss_left=20,
    )]


def _terror_eel() -> list[Monster]:
    """暗港精英 骇鳗: 140 血 (进阶 150), 撞击 -> 撕扯 循环.

    招式: 撞击 16 (进阶 18); 撕扯 3 段每段 3 (进阶 4), 附带 Vigor 6.
    固有 Shriek 70 与 Vigor 的"下一次攻击加成"未重建, 因此结果偏乐观.
    数值来源: Spire Codex (140 血 / 撞击 16 / 撕扯 3x3 / Shriek 70).
    """
    return [Monster(
        "骇鳗", hp=140, max_hp=140,
        script=[Intent("attack", damage=16), Intent("attack", damage=3, hits=3)],
        entity_id="TERROR_EEL_0",
    )]


def _phantasmal_gardeners() -> list[Monster]:
    """暗港精英 花园幽灵鳗: 4 只, 每只 27-32 血.

    招式: 啃咬 5 / 甩动 7 / 猛晃 1 点 3 段 / 变大 +3 力量 (各自循环).
    固有 Skittish 7 (每回合第一次被击中时获得格挡) 未重建, 结果偏乐观.
    数值来源: Spire Codex 与 Untapped (4 只, 合计 108-128 血).
    """
    script = [Intent("attack", damage=5), Intent("attack", damage=7),
              Intent("attack", damage=1, hits=3), Intent("buff", strength=3)]
    return [Monster("花园幽灵鳗", hp=28, max_hp=28, script=list(script),
                    entity_id=f"PHANTASMAL_GARDENER_{i}") for i in range(4)]


def _phrog_parasite() -> list[Monster]:
    """密林精英 异蛙寄生虫: 61-64 血, 固有寄生 4.

    行动循环: 感染(塞感染牌) -> 甩动(4x4).
    寄生: 死亡时召唤其他东西, 本模型未重建该阶段, 因此结果偏乐观.
    数值来源: Spire Codex (61-64 血, Infested 4, 感染 / 甩动 4x4).
    """
    return [Monster(
        "异蛙寄生虫", hp=64, max_hp=64,
        script=[Intent("custom", handler="add_infection", params={"count": 1, "pile": "discard"}),
                Intent("attack", damage=4, hits=4)],
        entity_id="PHROG_PARASITE_0",
    )]


ENCOUNTERS = {
    "nibbit": _nibbit,
    "slimes": _slimes,
    "jaw_worm": _jaw_worm,
    "lagavulin": _lagavulin,
    "ceremonial_beast": _ceremonial_beast,
    "vantom": _vantom,
    "bygone_effigy": _bygone_effigy,
    "byrdonis": _byrdonis,
    "skulking_colony": _skulking_colony,
    "terror_eel": _terror_eel,
    "phantasmal_gardeners": _phantasmal_gardeners,
    "phrog_parasite": _phrog_parasite,
}


@dataclass(frozen=True)
class Region:
    """一个幕的落地场景: 同幕的不同场景有各自的 Boss 池和精英池.

    精英只能在本场景的池子里出现, 用幕号直接汇总会把别的场景的精英算进来
    (第一幕的密林与暗港就是这样). 场景由当前幕的 Boss id 反推, 因为状态里
    只给了 Boss (地图节点) 而没有场景名.
    """

    act: int
    name: str
    bosses: tuple[str, ...]
    elites: tuple[str, ...]


# 来源: Spire Codex "各阶段的Boss与精英".
REGIONS: dict[str, Region] = {
    "overgrowth": Region(1, "密林", ("VANTOM", "CEREMONIAL_BEAST", "THE_KIN"),
                         ("bygone_effigy", "byrdonis", "phrog_parasite")),
    "underdocks": Region(1, "暗港",
                         ("WATERFALL_GIANT", "SOUL_FYSH", "LAGAVULIN_MATRIARCH"),
                         ("skulking_colony", "phantasmal_gardeners", "terror_eel")),
}


def region_of_boss(boss_id: str) -> str:
    """按 Boss id 前缀反推场景; 未收录时返回空字符串."""
    key = str(boss_id or "").upper()
    for region_id, region in REGIONS.items():
        if any(key.startswith(prefix) for prefix in region.bosses):
            return region_id
    return ""


def elites_for(act: int, boss_id: str = "") -> tuple[str, ...]:
    """本幕本场景会出现的精英.

    场景未知时: 该幕只有一个场景才返回它的精英; 一个幕对应多个场景时宁可返回空,
    也不能把另一个场景的精英当成可能遭遇.
    """
    region = REGIONS.get(region_of_boss(boss_id))
    if region is not None and region.act == act:
        return region.elites
    same_act = [r for r in REGIONS.values() if r.act == act]
    return same_act[0].elites if len(same_act) == 1 else ()


def encounter(name: str) -> list[Monster]:
    try:
        return ENCOUNTERS[name]()
    except KeyError:
        raise SystemExit(f"unknown encounter {name!r}; known: {', '.join(sorted(ENCOUNTERS))}")
