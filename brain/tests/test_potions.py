"""药水时机测试: 仅在改变结果时使用."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from brain.tools.combat_search import _Sim, _plan, decide_state


def state(*, hp=70, max_hp=70, block=0, energy=3, enemy_hp=40, incoming=10,
          hand=None, potions=None, enemies=1) -> dict:
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
        from brain.tools.combat_search import _Sim

        raw = state(hand=three_strikes())
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.apply_potion({"upgrade_hand": 1})
        self.assertEqual([c.damage for c in sim.hand], [9, 9, 9])

    def test_uses_bridge_numbers_when_available(self):
        from brain.model import GameState
        from brain.tools.combat_search import _Sim

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
        from brain.tools.combat_search import _Sim

        parsed = GameState.parse(state(enemy_hp=40, hand=three_strikes()))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.apply_potion({"duplicate_next": 1})
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 28)  # 两次各造成 6 点伤害

    def test_power_from_state_also_doubles(self):
        from brain.model import GameState
        from brain.tools.combat_search import _Sim

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
        hand = [{"index": 0, "name": "防御", "cost": "1", "block": 6,
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
        with patch("brain.tools.combat_search._plan", wraps=_plan) as search:
            with patch.object(_Sim, "build", wraps=_Sim.build) as build:
                for attempt in range(1, 3):
                    decision = decide_state(raw)
                    self.assertEqual(decision.action.kind, "play_card")
                    # 每次决策各有一次基础搜索和一次药水搜索, 状态也重新构建.
                    self.assertEqual(search.call_count, attempt * 2)
                    self.assertEqual(build.call_count, attempt)


if __name__ == "__main__":
    unittest.main()
