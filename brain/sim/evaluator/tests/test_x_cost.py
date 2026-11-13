"""X 费回归：验证动态费用、零次命中、群体目标和搜索与模拟结算的一致性。"""

import json
from pathlib import Path
import tempfile
import unittest

from brain.actions import PlayCard
from brain.combat.search import _Sim, decide_state
from brain.model import Card, GameState
from brain.sim.deck import card
from brain.sim.engine import DeckCard, Fight, Intent, Monster
from brain.sim.evaluator import evaluate
from brain.sim.evaluator.core import _ObservedFight
from brain.sim.evaluator.input import parse
from brain.sim.scenario_file import load


def whirlwind(upgraded=False):
    return {"index": 0, "id": "WHIRLWIND", "name": "旋风斩+" if upgraded else "旋风斩",
            "type": "Attack", "cost": "X", "damage": 8 if upgraded else 5,
            "hits": 99, "target_type": "AllEnemies", "can_play": True,
            "is_upgraded": upgraded}


def state(energy=3, hand=None, enemies=2):
    return GameState.parse({
        "state_type": "monster",
        "player": {"hp": 80, "max_hp": 80, "energy": energy, "max_energy": 3,
                   "hand": hand if hand is not None else [whirlwind()]},
        "battle": {"round": 1, "turn": "player", "ready_for_action": True,
                   "enemies": [{"entity_id": f"E{i}", "name": f"木桩{i}", "hp": 100, "max_hp": 100,
                                "intents": [{"type": "Stun"}]} for i in range(enemies)]},
    })


def native(energy, cards=None, enemies=2):
    fight = Fight(cards if cards is not None else [card("旋风斩")],
                  [Monster(f"木桩{i}", 100, 100, [Intent("wait")]) for i in range(enemies)],
                  hp=80, max_hp=80, max_energy=energy)
    fight.start_turn()
    return fight


def play_named(fight, name):
    return fight.play(next(i for i, c in enumerate(fight.hand) if c.name == name))


class XCostTests(unittest.TestCase):
    def test_parser_retains_x_instead_of_defaulting_to_one_energy(self):
        parsed = Card.parse(whirlwind())
        self.assertEqual(parsed.cost, 0)
        self.assertTrue(parsed.cost_x)
        self.assertTrue(parsed.hits_x)
        self.assertFalse(parsed.needs_target)
        self.assertTrue(parsed.can_play)

    def test_unmodelled_x_effect_is_not_treated_as_fixed_cost(self):
        parsed = Card.parse({"name": "未知 X 效果", "cost": "X", "description": "获得X点格挡。"})
        self.assertTrue(parsed.cost_x)
        self.assertFalse(parsed.can_play)

    def test_explicit_fixed_cost_does_not_inherit_the_x_marker_by_name(self):
        raw = {**whirlwind(), "cost": "2", "hits": 2}
        parsed = Card.parse(raw)
        self.assertEqual((parsed.cost, parsed.hits), (2, 2))
        self.assertFalse(parsed.cost_x)
        self.assertFalse(parsed.hits_x)

    def test_search_and_native_use_remaining_energy_for_every_enemy(self):
        for energy in (0, 1, 3, 5):
            with self.subTest(energy=energy):
                current = state(energy)
                search = _Sim.build(current.player, current.battle)
                fight = native(energy)
                self.assertIn((0, None), search.legal())
                self.assertTrue(search.play(0, None))
                self.assertTrue(fight.play(0))
                expected = [100 - 5 * energy] * 2
                self.assertEqual((search.energy, [e.hp for e in search.enemies]), (0, expected))
                self.assertEqual((fight.energy, [e.hp for e in fight.monsters]), (0, expected))

    def test_upgraded_x_card_uses_upgraded_damage_per_hit(self):
        current = state(4, [whirlwind(upgraded=True)])
        search = _Sim.build(current.player, current.battle)
        fight = native(4, [card("旋风斩+")])
        search.play(0, None)
        fight.play(0)
        self.assertEqual([e.hp for e in search.enemies], [68, 68])
        self.assertEqual([e.hp for e in fight.monsters], [68, 68])

    def test_strength_and_vulnerable_apply_to_each_x_hit(self):
        raw = state(3).raw
        raw["player"]["status"] = [{"id": "STRENGTH_POWER", "amount": 2}]
        raw["battle"]["enemies"][0]["status"] = [{"id": "VULNERABLE_POWER", "amount": 1}]
        current = GameState.parse(raw)
        search = _Sim.build(current.player, current.battle)
        fight = native(3)
        fight.strength = 2
        fight.monsters[0].vulnerable = 1
        search.play(0, None)
        fight.play(0)
        self.assertEqual([e.hp for e in search.enemies], [70, 79])
        self.assertEqual([e.hp for e in fight.monsters], [70, 79])

    def test_spending_energy_before_x_card_reduces_its_hits(self):
        strike = {"index": 1, "name": "打击", "type": "Attack", "cost": "1", "damage": 6,
                  "target_type": "AnyEnemy"}
        current = state(4, [whirlwind(), strike])
        search = _Sim.build(current.player, current.battle)
        fight = native(4, [card("旋风斩"), card("打击")])
        search.play(1, 0)
        search.play(0, None)
        play_named(fight, "打击")
        play_named(fight, "旋风斩")
        self.assertEqual([e.hp for e in search.enemies], [79, 85])
        self.assertEqual([e.hp for e in fight.monsters], [79, 85])
        self.assertEqual((search.energy, fight.energy), (0, 0))

    def test_gaining_energy_before_x_card_increases_its_hits(self):
        gain = {"index": 1, "name": "产能", "type": "Skill", "cost": "0", "energy": 2,
                "target_type": "Self"}
        current = state(1, [whirlwind(), gain])
        search = _Sim.build(current.player, current.battle)
        fight = native(1, [card("旋风斩"), DeckCard("产能", cost=0, energy=2, target="Self")])
        search.play(1, None)
        search.play(0, None)
        play_named(fight, "产能")
        play_named(fight, "旋风斩")
        self.assertEqual([e.hp for e in search.enemies], [85, 85])
        self.assertEqual([e.hp for e in fight.monsters], [85, 85])

    def test_sibling_search_branches_do_not_capture_one_shared_x_value(self):
        current = state(4)
        original = _Sim.build(current.player, current.battle)
        low, high = original.clone(), original.clone()
        low.energy, high.energy = 1, 4
        low.play(0, None)
        high.play(0, None)
        self.assertEqual((low.enemies[0].hp, high.enemies[0].hp), (95, 80))
        self.assertEqual((original.energy, original.enemies[0].hp), (4, 100))

    def test_duplicate_uses_same_x_without_spending_energy_twice(self):
        current = state(3)
        search = _Sim.build(current.player, current.battle)
        search.duplicate_next = 1
        search.play(0, None)
        self.assertEqual(search.energy, 0)
        self.assertEqual([e.hp for e in search.enemies], [70, 70])

    def test_zero_x_has_no_damage_but_still_counts_as_an_attack_card(self):
        fight = native(0, [card("狂怒"), card("旋风斩")])
        play_named(fight, "狂怒")
        play_named(fight, "旋风斩")
        self.assertEqual([e.hp for e in fight.monsters], [100, 100])
        self.assertEqual(fight.block, 3)

    def test_action_validation_and_state_export_preserve_x(self):
        fight = native(0)
        raw = fight.to_state()
        self.assertEqual(raw["player"]["hand"][0]["cost"], "X")
        parsed = GameState.parse(raw)
        action = PlayCard(parsed.battle.hand[0])
        action.check(parsed)
        self.assertIn("费X", action.describe())
        self.assertNotIn("cost", action.to_payload())

    def test_real_search_chooses_group_attack_without_a_single_target(self):
        decision = decide_state(state(3).raw)
        self.assertEqual(decision.action.kind, "play_card")
        self.assertIsNone(decision.action.target)

    def test_evaluator_records_actual_cost_and_excludes_x_from_printed_average(self):
        report = evaluate({"deck": ["旋风斩"], "runs": 1, "turns": 1, "base_energy": 3,
                           "incoming_by_turn": 0})
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["dimensions"]["damage"]["by_turn"][0]["mean"], 15)
        costs = report["dimensions"]["cost"]
        self.assertEqual(costs["energy_spent"][0]["mean"], 3)
        self.assertEqual(costs["energy_remaining"][0]["mean"], 0)
        self.assertIsNone(costs["printed_average"])
        self.assertEqual(costs["x_cost_cards"], 1)

    def test_explicit_x_input_is_supported_without_guessing_unknown_effects(self):
        known = evaluate({"deck": [{"name": "X 攻击", "cost": "X", "hits_x": True, "damage": 6}],
                          "runs": 1, "turns": 1, "base_energy": 2})
        unknown = evaluate({"deck": [{"name": "X 防御", "cost": "X", "block": 6}], "runs": 1})
        self.assertEqual(known["dimensions"]["damage"]["by_turn"][0]["mean"], 12)
        self.assertEqual(unknown["status"], "unsupported")

    def test_observed_automatic_play_does_not_charge_x_energy(self):
        fight = _ObservedFight(parse({"deck": ["旋风斩"], "runs": 1, "base_energy": 3}), 0)
        fight.start_turn()
        self.assertTrue(fight.play(0, automatic=True))
        self.assertEqual((fight.energy, fight.row["energy_spent"], fight.row["damage"]), (3, 0, 15))

    def test_armaments_keeps_x_cost_and_upgrades_per_hit_damage(self):
        fight = _ObservedFight(parse({"deck": ["武装+", "旋风斩"], "runs": 1, "base_energy": 4}), 0)
        fight.start_turn()
        play_named(fight, "武装+")
        play_named(fight, "旋风斩+")
        self.assertEqual((fight.energy, fight.row["damage"]), (0, 24))

    def test_json_scenario_preserves_x_cost_and_group_target(self):
        raw = {"name": "X 费场景", "player": {"hp": 80, "max_hp": 80, "energy": 4, "max_energy": 4, "deck": [
            {"name": "旋风斩", "cost": "X", "damage": 5}]},
            "enemies": [{"name": "木桩", "hp": 100, "intents": [{"type": "wait"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(raw, ensure_ascii=False))
            scenario = load(path)
        self.assertTrue(scenario.deck[0].cost_x)
        self.assertTrue(scenario.deck[0].hits_x)
        self.assertEqual(scenario.deck[0].target, "AllEnemies")


if __name__ == "__main__":
    unittest.main()
