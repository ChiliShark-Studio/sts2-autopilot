"""药水时机测试: 仅在改变结果时使用."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from brain.combat.search import _Sim, _plan, decide_state


def state(*, hp=70, max_hp=70, block=0, energy=3, enemy_hp=40, incoming=10,
          hand=None, potions=None, enemies=1, draw=None) -> dict:
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [
                {
                    "entity_id": f"E{i}", "name": f"怪{i}", "hp": enemy_hp, "max_hp": enemy_hp,
                    "intents": [{"type": "Attack", "damage": incoming, "hits": 1}],
                }
                for i in range(enemies)
            ],
        },
        "player": {
            "hp": hp, "max_hp": max_hp, "block": block, "energy": energy,
            "hand": hand if hand is not None else [
                {"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1}],
            "potions": potions or [],
            "draw_pile": draw or [],
        },
    }


def fire_potion(slot=0):
    return {"slot": slot, "id": "FIRE_POTION", "name": "火焰药水",
            "description": "造成20点伤害。", "target_type": "AnyEnemy",
            "can_use_in_combat": True}


def block_potion(slot=0):
    return {"slot": slot, "id": "BLOCK_POTION", "name": "格挡药水",
            "description": "获得12点格挡。", "target_type": "Self",
            "can_use_in_combat": True}


def strength_potion(slot=0):
    return {"slot": slot, "id": "FLEX_POTION", "name": "肌肉药水",
            "description": "获得5点力量。在你的这个回合结束时，失去5点力量。",
            "target_type": "AnyPlayer", "can_use_in_combat": True}


def three_strikes():
    return [
        {"index": i, "name": "打击", "cost": "1", "damage": 6, "hits": 1, "target_type": "AnyEnemy"}
        for i in range(3)
    ]


class BuffPotionTests(unittest.TestCase):
    def test_strength_potion_used_in_dangerous_fight(self):
        # 3 次攻击各造成 6 点伤害; 增加 5 点力量可多造成 15 点伤害, 血量为 43% 时值得用药.
        decision = decide_state(state(hp=30, enemy_hp=40, hand=three_strikes(),
                                      potions=[strength_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("增伤", decision.reason)

    def test_strength_potion_saved_when_healthy(self):
        decision = decide_state(state(hp=70, enemy_hp=40, hand=three_strikes(),
                                      potions=[strength_potion()]))
        self.assertEqual(decision.action.kind, "play_card")


def forge_potion(slot=0):
    return {"slot": slot, "id": "BLESSING_OF_THE_FORGE", "name": "熔炉的祝福",
            "description": "在本场战斗中升级你手牌中的所有牌。",
            "target_type": "Self", "can_use_in_combat": True}


class CardEnhancementTests(unittest.TestCase):
    def test_parse_flags_upgrade_hand(self):
        from brain.effects import parse_potion

        eff = parse_potion("在本场战斗中升级你手牌中的所有牌。")
        self.assertEqual(eff["upgrade_hand"], 1)
        self.assertEqual(eff["damage"], 0)

    def test_apply_upgrades_every_hand_card(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        raw = state(hand=three_strikes())
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.apply_potion({"upgrade_hand": 1})
        self.assertEqual([c.damage for c in sim.hand], [9, 9, 9])

    def test_uses_bridge_numbers_when_available(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        hand = [{"index": 0, "name": "打击", "cost": "1", "damage": 6,
                 "hits": 1, "target_type": "AnyEnemy", "damage_upgraded": 12}]
        parsed = GameState.parse(state(hand=hand))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.apply_potion({"upgrade_hand": 1})
        self.assertEqual(sim.hand[0].damage, 12)  # 使用准确数值, 而非 6+3 的估计

    def test_potion_used_when_upgrade_gain_is_large(self):
        hand = [{"index": i, "name": "重击", "cost": "1", "damage": 6,
                 "hits": 1, "target_type": "AnyEnemy", "damage_upgraded": 30}
                for i in range(3)]
        decision = decide_state(state(hp=30, enemy_hp=60, hand=hand, potions=[forge_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        # 三张牌全部升级后本回合可以斩杀, 因此报告斩杀.
        self.assertIn("熔炉的祝福", decision.reason)


def duplicate_potion(slot=0):
    return {"slot": slot, "id": "DUPLICATION_POTION", "name": "复制药水",
            "description": "本回合，你打出的下一张牌会额外打出一次。",
            "target_type": "Self", "can_use_in_combat": True}


class DuplicationTests(unittest.TestCase):
    def test_parse_flags_duplicate(self):
        from brain.effects import parse_potion

        eff = parse_potion("本回合，你打出的下一张牌会额外打出一次。")
        self.assertEqual(eff["duplicate_next"], 1)

    def test_next_card_is_played_twice(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        parsed = GameState.parse(state(enemy_hp=40, hand=three_strikes()))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.apply_potion({"duplicate_next": 1})
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 28)  # 两次各造成 6 点伤害

    def test_power_from_state_also_doubles(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        raw = state(enemy_hp=40, hand=three_strikes())
        raw["player"]["status"] = [{"id": "DUPLICATION_POWER", "name": "复制", "amount": 1}]
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertEqual(sim.duplicate_next, 1)
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 28)

    def test_used_when_it_enables_lethal(self):
        # 打击造成 6 点伤害, 对方剩余 12 点生命, 不复制就无法斩杀.
        hand = [{"index": 0, "name": "打击", "cost": "1", "damage": 6,
                 "hits": 1, "target_type": "AnyEnemy"}]
        decision = decide_state(state(enemy_hp=12, hp=30, hand=hand,
                                      potions=[duplicate_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("复制药水", decision.reason)


class PotionTests(unittest.TestCase):
    def test_no_potion_plays_a_card(self):
        decision = decide_state(state())
        self.assertEqual(decision.action.kind, "play_card")

    def test_uses_block_potion_to_survive_lethal(self):
        # 剩余 10 点生命, 即将承受 15 点伤害, 不用药就会死亡.
        decision = decide_state(state(hp=10, incoming=15, potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertEqual(decision.action.slot, 0)
        self.assertIn("保命", decision.reason)

    def test_ignores_block_potion_when_survivable(self):
        decision = decide_state(state(hp=70, incoming=15, potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_uses_damage_potion_to_finish_single_enemy(self):
        # 手牌造成 6 点伤害, 对方剩余 20 点生命, 火焰药水造成 20 点伤害, 可以斩杀.
        decision = decide_state(state(enemy_hp=20, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertEqual(decision.action.target, "E0")
        self.assertIn("斩杀", decision.reason)

    def test_does_not_waste_potion_when_hand_already_lethal(self):
        decision = decide_state(state(enemy_hp=6, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_damage_potion_skipped_when_it_would_not_finish(self):
        # 手牌伤害 6, 药水伤害 20, 敌方生命 40, 仍无法斩杀, 因此保留药水.
        decision = decide_state(state(enemy_hp=40, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_multi_enemy_skips_finisher_logic(self):
        decision = decide_state(state(enemy_hp=10, enemies=2, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_unusable_potion_is_ignored(self):
        potion = dict(block_potion(), can_use_in_combat=False)
        decision = decide_state(state(hp=10, incoming=15, potions=[potion]))
        self.assertEqual(decision.action.kind, "play_card")


class PotionPlanningTests(unittest.TestCase):
    def test_saves_block_potion_when_cards_already_survive(self):
        # 手牌已经挡住全部伤害时, 药水不带来任何减伤, 应留到后面的战斗.
        hand = [{"index": 0, "name": "防御", "cost": "1", "block": 20,
                 "target_type": "Self"}]
        decision = decide_state(state(hp=10, incoming=15, energy=1, hand=hand,
                                      potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "防御")

    def test_block_potion_and_card_together_prevent_death(self):
        # 药水提供 12 格挡, 防御提供 10 格挡, 合计才能挡住致命伤害.
        hand = [{"index": 0, "name": "防御", "cost": "1", "block": 10,
                 "target_type": "Self"}]
        decision = decide_state(state(hp=10, incoming=28, energy=1, hand=hand,
                                      potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("保命", decision.reason)

    def test_damage_potion_and_attack_together_prevent_death(self):
        # 药水单独无法击杀, 但配合手牌可以在敌人攻击前结束战斗.
        decision = decide_state(state(hp=10, incoming=20, enemy_hp=26,
                                      potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("保命", decision.reason)

    def test_empty_hand_can_use_damage_potion_to_win(self):
        decision = decide_state(state(enemy_hp=20, hand=[], potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("斩杀", decision.reason)

    def test_empty_hand_can_use_block_potion_to_survive(self):
        decision = decide_state(state(hp=10, incoming=15, hand=[],
                                      potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("保命", decision.reason)

    def test_empty_hand_without_help_ends_turn(self):
        self.assertEqual(decide_state(state(hand=[])).action.kind, "end_turn")

    def test_waits_when_combat_is_not_ready_even_with_potion(self):
        raw = state(hp=10, incoming=15, hand=[], potions=[block_potion()])
        raw["battle"]["ready_for_action"] = False
        self.assertIsNone(decide_state(raw).action)

    def test_targets_small_attacker_instead_of_largest_enemy(self):
        raw = state(hp=10, incoming=0, energy=1, enemies=2, potions=[fire_potion()])
        small = raw["battle"]["enemies"][1]
        small["hp"] = small["max_hp"] = 10
        small["intents"][0]["damage"] = 20
        decision = decide_state(raw)
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertEqual(decision.action.target, "E1")
        self.assertIn("保命", decision.reason)

    def test_keeps_potion_when_no_plan_survives(self):
        decision = decide_state(state(hp=10, incoming=40, enemy_hp=80,
                                      potions=[block_potion()]))
        self.assertNotEqual(decision.action.kind, "use_potion")

    def test_reuses_search_only_within_current_decision(self):
        raw = state(enemy_hp=40, potions=[fire_potion()])
        with patch("brain.combat.search._plan", wraps=_plan) as search:
            with patch.object(_Sim, "build", wraps=_Sim.build) as build:
                for attempt in range(1, 3):
                    decision = decide_state(raw)
                    self.assertEqual(decision.action.kind, "play_card")
                    # 每次决策各有一次基础搜索和一次药水搜索, 状态也重新构建.
                    self.assertEqual(search.call_count, attempt * 2)
                    self.assertEqual(build.call_count, attempt)


def dexterity_potion(slot=0):    return {"slot": slot, "id": "DEXTERITY_POTION", "name": "敏捷药水",
            "description": "获得2点敏捷。", "target_type": "AnyPlayer",
            "can_use_in_combat": True}


def defend(name="防御", block=5):
    return {"index": 0, "name": name, "cost": "1", "block": block,
            "target_type": "AnyPlayer", "description": f"获得{block}点格挡。"}


def energy_potion(slot=0):
    return {"slot": slot, "id": "ENERGY_POTION", "name": "能量药水",
            "description": "获得2点能量。", "target_type": "AnyPlayer",
            "can_use_in_combat": True}


class EnergyPotionTests(unittest.TestCase):
    """能量药水的价值是多打一张牌, 必须计入模拟, 否则等于不存在."""

    def test_parse_reads_energy(self):
        from brain.effects import parse_potion

        self.assertEqual(parse_potion("获得2点能量。")["energy"], 2)

    def test_parse_reads_energy_icons(self):
        from brain.effects import parse_potion

        # 实际游戏把数量渲染成图标: 两个图标 = 2 点能量.
        desc = "获得[ironclad_energy_icon.png][ironclad_energy_icon.png]。"
        self.assertEqual(parse_potion(desc)["energy"], 2)

    def test_parse_reads_binding_potion(self):
        from brain.effects import parse_potion

        eff = parse_potion("给予所有敌人1层虚弱和1层易伤。")
        self.assertEqual((eff["weak"], eff["vulnerable"]), (1, 1))

    def test_energy_potion_enables_lethal(self):
        # 1 点能量只能打一张打击(6 伤); 加 2 点能量后三张全打, 18 点可以斩杀.
        decision = decide_state(state(hp=30, enemy_hp=17, energy=1,
                                      hand=three_strikes(), potions=[energy_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("能量药水", decision.reason)


class DexterityTests(unittest.TestCase):
    """敏捷提高每张牌的格挡, 必须被模拟, 否则药水等同不存在."""

    def test_parse_reads_dexterity(self):
        from brain.effects import parse_potion

        eff = parse_potion("获得2点敏捷。")
        self.assertEqual(eff["dexterity"], 2)
        self.assertEqual(eff["strength"], 0)

    def test_applied_dexterity_adds_block_to_each_card(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        parsed = GameState.parse(state(hand=[defend(), defend()]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.apply_potion({"dexterity": 2})
        sim.play(0, None)
        sim.play(0, None)
        self.assertEqual(sim.block, 14)  # (5+2) * 2

    def test_survives_lethal_only_with_dexterity_potion(self):
        # 两张防御共 10 点格挡, 8 血吃 20 点伤害会死亡; 加 2 点敏捷后共 14 点, 剩 2 血.
        hand = [dict(defend(), index=0), dict(defend(), index=1),
                {"index": 2, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
                 "target_type": "AnyEnemy"}]
        raw = state(hp=8, incoming=20, enemy_hp=40, energy=2, hand=hand,
                    potions=[dexterity_potion()])
        decision = decide_state(raw)
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("敏捷药水", decision.reason)
        self.assertIn("保命", decision.reason)

    def test_used_when_it_prevents_enough_damage(self):
        # 三张防御共 15 点格挡, 20 点攻击仍掉 5 血; 加 2 点敏捷后完全挡住.
        # 35/80 血属于危险区间, 少掉 5 点血足以消耗一瓶防御药水.
        hand = [dict(defend(), index=i) for i in range(3)]
        raw = state(hp=35, max_hp=80, incoming=20, enemy_hp=40, energy=3,
                    hand=hand, potions=[dexterity_potion()])
        decision = decide_state(raw)
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertIn("减伤", decision.reason)

    def test_dexterity_potion_saved_when_already_safe(self):
        hand = [dict(defend(), index=0), dict(defend(), index=1)]
        raw = state(hp=70, incoming=20, enemy_hp=40, energy=2, hand=hand,
                    potions=[dexterity_potion()])
        self.assertNotEqual(decide_state(raw).action.kind, "use_potion")


def swift_potion(slot=0):
    return {"slot": slot, "id": "SWIFT_POTION", "name": "迅捷药水",
            "description": "抽3张牌。", "target_type": "AnyPlayer",
            "can_use_in_combat": True}


def colorless_potion(slot=0):
    return {"slot": slot, "id": "COLORLESS_POTION", "name": "无色药水",
            "description": "从3张随机无色牌中选择1张加入你的手牌。这张牌在本回合可以免费打出。",
            "target_type": "AnyPlayer", "can_use_in_combat": True}


class DrawPotionTests(unittest.TestCase):
    """抽牌类药水无法本地模拟: 桥接按稀有度排序输出牌堆, 顺序不是抽牌顺序."""

    def test_draw_potion_is_not_modelled_locally(self):
        from brain.effects import parse_potion
        from brain.combat.search import potion_effects_known

        eff = parse_potion("抽3张牌。")
        self.assertEqual(eff["draw"], 3)
        # 抽到哪几张无法确定, 因此不计入本地收益, 交给模型判断.
        self.assertFalse(potion_effects_known(eff))

    def test_draw_potion_is_not_faked_as_value(self):
        decision = decide_state(state(hp=40, enemy_hp=17, energy=3, hand=[],
                                      potions=[swift_potion()]))
        self.assertNotEqual(decision.action.kind, "use_potion")

    def test_colorless_potion_is_flagged_unknown(self):
        from brain.effects import parse_potion

        eff = parse_potion(colorless_potion()["description"])
        self.assertEqual(eff["random_card"], 1)
        # 效果未知时不编造数值: 没有可模拟的收益, 搜索不会替它做决定.
        self.assertEqual(eff["damage"] + eff["block"] + eff["draw"] + eff["energy"], 0)

    def test_colorless_potion_is_not_faked_as_damage(self):
        decision = decide_state(state(hp=40, enemy_hp=5, energy=0, hand=[],
                                      potions=[colorless_potion()]))
        self.assertFalse(decision.action is not None and decision.action.kind == "use_potion")


class BloodlettingTests(unittest.TestCase):
    """放血: 0 费, 失去 3 点生命换 2 点能量; 两项都必须被解析."""

    def test_parse_reads_energy_icons_and_hp_cost(self):
        from brain.effects import parse_card

        parsed = parse_card({
            "name": "放血", "cost": "0", "type": "Skill",
            "description": "失去3点生命。 获得[ironclad_energy_icon.png][ironclad_energy_icon.png]。",
        })
        self.assertEqual(parsed["cost"], 0)
        self.assertEqual(parsed["energy"], 2)
        self.assertEqual(parsed["hp_cost"], 3)

    def test_playing_it_trades_hp_for_energy(self):
        from brain.model import GameState
        from brain.combat.search import _Sim

        raw = state(hp=40, energy=0, hand=[{
            "index": 0, "name": "放血", "cost": "0",
            "description": "失去3点生命。 获得[ironclad_energy_icon.png][ironclad_energy_icon.png]。",
        }])
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(0, None)
        self.assertEqual(sim.energy, 2)
        self.assertEqual(sim.hp, 37)

    def test_energy_from_bloodletting_enables_attack(self):
        # 0 能量时放血是唯一能打出的牌, 换来 2 费继续输出.
        hand = [{"index": 0, "name": "放血", "cost": "0",
                 "description": "失去3点生命。 获得[ironclad_energy_icon.png][ironclad_energy_icon.png]。"},
                {"index": 1, "name": "打击", "cost": "1", "damage": 6, "hits": 1,
                 "target_type": "AnyEnemy"}]
        decision = decide_state(state(hp=40, energy=0, enemy_hp=6, hand=hand))
        self.assertEqual(decision.action.kind, "play_card")
        self.assertEqual(decision.action.card.name, "放血")


class FairyPotionTests(unittest.TestCase):
    """瓶中精灵是阵亡复活, 被动生效, 必须计入"这回合会不会死"."""

    FAIRY = {"slot": 2, "id": "FAIRY_IN_A_BOTTLE", "name": "瓶中精灵",
             "description": "当你的生命值将被减少至0或以下时，改为丢弃这瓶药水，"
                            "然后将你回复到你最大生命值的30%。",
             "target_type": "Self", "can_use_in_combat": False}

    def test_parse_reads_death_save(self):
        from brain.effects import parse_potion

        self.assertEqual(parse_potion(self.FAIRY["description"])["death_save"], 30)

    def test_lethal_turn_is_not_treated_as_death(self):
        from brain.model import GameState
        from brain.combat.search import _Sim, _plan

        # 10 血吃 30 点伤害: 有瓶中精灵时不会真的死, 方案终点应带回血量.
        raw = state(hp=10, incoming=30, max_hp=80, energy=0, hand=[],
                    potions=[self.FAIRY])
        st = GameState.parse(raw)
        sim = _Sim.build(st.player, st.battle)
        self.assertEqual(sim.revive_pct, 30)
        self.assertGreater(_plan(sim, 10, 7).hp_left, 0)

    def test_without_fairy_the_same_turn_is_fatal(self):
        from brain.model import GameState
        from brain.combat.search import _Sim, _plan

        raw = state(hp=10, incoming=30, max_hp=80, energy=0, hand=[], potions=[])
        st = GameState.parse(raw)
        sim = _Sim.build(st.player, st.battle)
        self.assertLessEqual(_plan(sim, 10, 7).hp_left, 0)


if __name__ == "__main__":
    unittest.main()
