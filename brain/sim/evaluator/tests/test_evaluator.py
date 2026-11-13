"""验证实际结算、覆盖检查及统计口径，不把提示词字面内容当作策略有效性。"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brain.sim.evaluator import compare, evaluate, fight_boss
from brain.sim.evaluator.core import _ObservedFight
from brain.sim.evaluator.input import parse


def request(deck, **kwargs):
    return {"deck": deck, "runs": 1, "turns": 2, "incoming_by_turn": 0, **kwargs}


class EvaluationTests(unittest.TestCase):
    def test_four_dimensions_are_reproducible_and_input_is_unchanged(self):
        raw = request(["打击", "防御"], runs=2)
        before = copy.deepcopy(raw)
        a = evaluate(raw)
        self.assertEqual(a, evaluate(raw))
        self.assertEqual(raw, before)
        self.assertEqual(set(a["dimensions"]), {"damage", "defense", "cost", "startup"})
        self.assertEqual(a["dimensions"]["damage"]["by_turn"][0]["mean"], 6)
        self.assertEqual(a["dimensions"]["startup"]["first_cycle"]["mean"], 1)

    def test_spending_cannot_exceed_available_energy(self):
        result = evaluate(request([{"name": "攻击", "cost": 2, "damage": 10, "count": 5}], base_energy=3))
        costs = result["dimensions"]["cost"]
        self.assertEqual(costs["energy_spent"][0]["mean"], 2)
        self.assertEqual(costs["energy_remaining"][0]["mean"], 1)
        self.assertEqual(result["dimensions"]["damage"]["by_turn"][0]["mean"], 10)

    def test_relic_energy_varies_by_turn_and_is_not_double_counted(self):
        raw = request([{"name": "攻击", "cost": 1, "damage": 6, "count": 5}],
                      relics=[{"name": "首回合加费", "effects": {"energy_by_turn": [1, 0]}}])
        result = evaluate(raw)
        self.assertEqual(result["context"]["external_energy_by_turn"], [4, 3])
        self.assertEqual([v["mean"] for v in result["dimensions"]["damage"]["by_turn"]], [24, 18])
        raw["energy_by_turn"] = [4, 3]
        with self.assertRaises(ValueError):
            evaluate(raw)

    def test_card_energy_is_tracked_separately(self):
        result = evaluate(request([{"name": "产能", "cost": 0, "energy": 2, "exhaust": True},
                                   {"name": "攻击", "cost": 4, "damage": 30}]))
        costs = result["dimensions"]["cost"]
        self.assertEqual(costs["energy_start"][0]["mean"], 3)
        self.assertEqual(costs["energy_generated"][0]["mean"], 2)
        self.assertEqual(costs["energy_spent"][0]["mean"], 4)

    def test_generated_block_is_not_the_same_as_effective_block(self):
        result = evaluate(request([{"name": "格挡", "cost": 1, "block": 20}], incoming_by_turn=8))
        defense = result["dimensions"]["defense"]
        self.assertEqual(defense["block_generated"][0]["mean"], 20)
        self.assertEqual(defense["effective_block"][0]["mean"], 8)
        self.assertEqual(defense["enemy_hp_loss"][0]["mean"], 0)

    def test_self_damage_and_enemy_damage_are_separate(self):
        result = evaluate(request([{"name": "烙印", "cost": 0, "strength": 1, "hp_cost": 1}, "打击"],
                                  incoming_by_turn=5))
        defense = result["dimensions"]["defense"]
        self.assertEqual(defense["self_hp_cost"][0]["mean"], 1)
        self.assertEqual(defense["enemy_hp_loss"][0]["mean"], 5)

    def test_early_victory_does_not_create_zero_turns(self):
        result = evaluate(request(["打击"], enemy_hp=6))
        self.assertEqual(result["outcomes"]["win"], 1)
        self.assertEqual(result["dimensions"]["damage"]["by_turn"][1]["samples"], 0)
        self.assertIsNone(result["dimensions"]["damage"]["horizon_average"]["mean"])

    def test_death_and_unobserved_startup_are_visible(self):
        result = evaluate(request([{"name": "能力", "type": "power", "cost": 9, "strength": 2}],
                                  hp=1, incoming_by_turn=10))
        self.assertEqual(result["outcomes"]["death"], 1)
        timing = result["dimensions"]["startup"]["key_cards"]["能力"]["first_play"]
        self.assertIsNone(timing["mean"])
        self.assertEqual(timing["not_observed"], 1)

    def test_unknown_card_effect_and_relic_refuse_full_evaluation(self):
        for raw in (request(["不存在的牌"]),
                    request([{"name": "特殊牌", "cost": 1, "teleport": True}]),
                    request(["打击"], relics=[{"name": "未知遗物", "description": "神秘效果"}]),
                    request(["头槌"])):
            result = evaluate(raw)
            self.assertEqual(result["status"], "unsupported")
            self.assertTrue(result["coverage"]["missing"])
            self.assertIsNone(result["dimensions"]["damage"])

    def test_incomplete_action_does_not_become_a_zero_damage_sample(self):
        with patch("brain.sim.evaluator.core.decide_state", return_value=SimpleNamespace(action=None)):
            result = evaluate(request(["打击"]))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["outcomes"]["incomplete"], 1)
        self.assertEqual(result["dimensions"]["damage"]["by_turn"][0]["samples"], 0)

    def test_comparison_includes_mean_deltas_without_mutating_deck(self):
        raw = request(["打击"], base_energy=5)
        result = compare(raw, {"name": "攻击", "cost": 1, "damage": 10})
        self.assertEqual(len(raw["deck"]), 1)
        self.assertEqual(result["mean_delta"]["damage"]["by_turn"], [10, 10])

    def test_json_command_reports_failure_for_unsupported_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.json"
            path.write_text(json.dumps(request(["未知牌"])), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "brain.sim.evaluator", "--from", str(path)],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["status"], "unsupported")

    def test_json_command_evaluates_a_supported_deck(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.json"
            path.write_text(json.dumps(request(["打击"])), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "brain.sim.evaluator", "--from", str(path)],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")


class ObservationTests(unittest.TestCase):
    def test_power_leaves_deck_and_does_not_stack_each_cycle(self):
        fight = _ObservedFight(parse(request(["燃烧", "打击"], base_energy=5)), 0)
        fight.start_turn()
        pos = next(i for i, c in enumerate(fight.hand) if c.name == "燃烧")
        self.assertTrue(fight.play(pos))
        fight.finish_turn(True)
        fight.turn = 2
        fight.start_turn()
        self.assertNotIn("燃烧", [c.name for c in fight.hand])
        self.assertEqual(fight.strength, 2)

    def test_retained_block_is_only_counted_when_consumed(self):
        fight = _ObservedFight(parse(request(["壁垒", "防御"], base_energy=5, incoming_by_turn=3)), 0)
        fight.start_turn()
        for name in ("壁垒", "防御"):
            self.assertTrue(fight.play(next(i for i, c in enumerate(fight.hand) if c.name == name)))
        fight.finish_turn(True)
        fight.turn = 2
        fight.start_turn()
        fight.finish_turn(True)
        self.assertEqual([r["effective_block"] for r in fight.records], [3, 2])
        self.assertEqual([r["block_generated"] for r in fight.records], [5, 0])

    def test_corruption_changes_skills_but_not_power_cost(self):
        fight = _ObservedFight(parse(request(["腐化", "燃烧", "防御"], base_energy=5)), 0)
        fight.start_turn()
        for name in ("腐化", "燃烧", "防御"):
            self.assertTrue(fight.play(next(i for i, c in enumerate(fight.hand) if c.name == name)))
        self.assertEqual(fight.energy, 1)
        self.assertEqual(fight.exhausted, 1)
        self.assertEqual(fight.first_trigger["腐化"], 1)

    def test_draw_and_play_are_distinct_and_late_effects_wait(self):
        fight = _ObservedFight(parse(request(["恶魔形态", "打击"], base_energy=5)), 0)
        fight.start_turn()
        self.assertIn("恶魔形态", fight.first_draw)
        self.assertNotIn("恶魔形态", fight.first_play)
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "恶魔形态"))
        self.assertNotIn("恶魔形态", fight.first_trigger)
        fight.finish_turn(True)
        fight.turn = 2
        fight.start_turn()
        self.assertEqual(fight.first_trigger["恶魔形态"], 2)

    def test_retained_energy_is_available_on_next_turn(self):
        raw = request([{"name": "重击", "cost": 6, "damage": 30}],
                      relics=[{"name": "保留能量", "effects": {"carry_energy": True}}])
        result = evaluate(raw)
        self.assertEqual([v["mean"] for v in result["dimensions"]["cost"]["energy_start"]], [3, 6])
        self.assertEqual(result["dimensions"]["damage"]["by_turn"][1]["mean"], 30)


class ArmamentsTests(unittest.TestCase):
    def test_upgrades_one_card_using_existing_selection_score_and_persists(self):
        raw = request(["武装", "打击", "防御"], base_energy=5)
        fight = _ObservedFight(parse(raw), 0)
        fight.start_turn()
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "武装"))
        self.assertEqual([c.name for c in fight.hand if c.name.endswith("+")], ["打击+"])
        upgraded = next(c for c in fight.hand if c.name == "打击+")
        self.assertEqual(upgraded.damage, 9)
        self.assertEqual(len(fight.row["upgraded"]), 1)
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "打击+"))
        fight.finish_turn(True)
        fight.turn = 2
        fight.start_turn()
        self.assertIn("打击+", [c.name for c in fight.hand])
        self.assertEqual(raw["deck"], ["武装", "打击", "防御"])

    def test_upgraded_armaments_upgrades_all_eligible_hand_cards_once(self):
        fight = _ObservedFight(parse(request(["武装+", "打击", "防御", "痛击+"], base_energy=5)), 0)
        fight.start_turn()
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "武装+"))
        self.assertEqual(sorted(c.name for c in fight.hand), ["打击+", "痛击+", "防御+"])
        self.assertEqual(len(fight.row["upgraded"]), 2)
        fight._upgrade_hand(True)
        self.assertEqual(len(fight.row["upgraded"]), 2)

    def test_unknown_upgrade_rules_are_not_guessed(self):
        result = evaluate(request(["武装", {"name": "自定义攻击", "cost": 1, "damage": 20}]))
        self.assertEqual(result["status"], "unsupported")
        self.assertTrue(any("升级规则" in item for item in result["coverage"]["missing"]))

    def test_verified_outside_combat_relics_do_not_add_energy_or_heal_in_combat(self):
        raw = request(["武装", "打击"], incoming_by_turn=10, relics=[
            {"id": "BURNING_BLOOD", "name": "燃烧之血", "description": "在战斗结束时，回复6点生命。",
             "counter": None, "keywords": []},
            {"id": "POMANDER", "name": "橙型香盒", "description": "拾起时，升级一张牌。"},
        ])
        report = evaluate(raw)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["context"]["external_energy_by_turn"], [3, 3])
        self.assertTrue(any("战后回复" in s for s in report["coverage"]["assumptions"]))

    def test_changed_relic_description_requires_new_coverage(self):
        raw = request(["打击"], relics=[{"id": "BURNING_BLOOD", "name": "燃烧之血",
                                        "description": "每回合开始时，回复6点生命。"}])
        self.assertEqual(evaluate(raw)["status"], "unsupported")


class RingedDeckTests(unittest.TestCase):
    def make_fight(self, deck, **kwargs):
        fight = _ObservedFight(parse(request(deck, **kwargs)), 0)
        fight.start_turn()
        return fight

    def play_named(self, fight, name):
        self.assertTrue(fight.play(next(i for i, c in enumerate(fight.hand) if c.name == name)))

    def test_fixture_card_names_resolve_without_carrying_round_seven_statuses(self):
        source = Path(__file__).resolve().parents[3] / "tests/data/ringed_turn.json"
        hand = json.loads(source.read_text(encoding="utf-8"))["201"]["player"]["hand"]
        fight = self.make_fight([c["name"] for c in hand])
        self.assertEqual(fight.spec.missing, [])
        self.assertEqual(fight.strength, 0)
        self.assertFalse(fight.ringing_active)
        bash = next(c for c in fight.hand if c.name == "痛击+")
        self.assertEqual((bash.damage, bash.vulnerable), (10, 3))
        self.assertEqual(fight.spec.types[next(c.id for c in fight.hand if c.name == "凶恶")], "power")

    def test_vicious_draws_once_per_application_before_bash_enters_discard(self):
        fight = self.make_fight(["凶恶", "痛击+", "防御"])
        self.play_named(fight, "凶恶")
        fight.discard.append(fight.hand.pop(next(i for i, c in enumerate(fight.hand) if c.name == "防御")))
        drawn = fight.row["drawn"]
        self.play_named(fight, "痛击+")
        self.assertEqual(fight.row["drawn"] - drawn, 1)
        self.assertEqual([c.name for c in fight.hand], ["防御"])
        self.assertEqual([c.name for c in fight.discard], ["痛击+"])
        self.assertEqual(fight.monsters[0].vulnerable, 3)
        self.assertEqual(fight.first_trigger["凶恶"], 1)
        self.assertEqual(next(c.draw for c in fight.discard if c.name == "痛击+"), 0)

    def test_vicious_stacks_and_can_trigger_on_an_already_vulnerable_enemy(self):
        fight = self.make_fight(["凶恶", "凶恶", "痛击+", "防御", "打击"], base_energy=5)
        self.play_named(fight, "凶恶")
        self.play_named(fight, "凶恶")
        bash = next(c for c in fight.hand if c.name == "痛击+")
        fight.discard = [c for c in fight.hand if c is not bash]
        fight.hand = [bash]
        fight.monsters[0].vulnerable = 1
        drawn = fight.row["drawn"]
        self.play_named(fight, "痛击+")
        self.assertEqual(fight.row["drawn"] - drawn, 2)
        self.assertEqual(fight.monsters[0].vulnerable, 4)
        self.assertEqual(fight.draw_per_vulnerable, 2)

    def test_killed_enemy_does_not_trigger_vicious(self):
        fight = self.make_fight(["凶恶", "痛击+", "防御"], enemy_hp=10)
        self.play_named(fight, "凶恶")
        drawn = fight.row["drawn"]
        self.play_named(fight, "痛击+")
        self.assertEqual(fight.row["drawn"], drawn)
        self.assertNotIn("凶恶", fight.first_trigger)

    def test_thinking_ahead_draws_then_returns_selection_before_exhaust_draw(self):
        from brain.build.scoring import card_score
        from brain.model import Card

        fight = self.make_fight(["深谋远虑", "防御", "打击", "痛击+", "耸肩无视+"], draw_by_turn=3)
        cards = fight.hand + fight.draw_pile
        thinking = next(c for c in cards if c.name == "深谋远虑")
        others = [c for c in cards if c is not thinking]
        fight.hand, fight.draw_pile = [thinking] + others[:2], others[2:]
        expected = max(others, key=lambda c: card_score(Card.parse({
            "name": c.name, "cost": str(c.cost), "damage": c.damage,
            "block": c.block, "vulnerable": c.vulnerable, "draw": c.draw}))).id
        fight.draw_per_exhaust = 1
        drawn = fight.row["drawn"]
        self.play_named(fight, "深谋远虑")
        self.assertEqual(fight.row["drawn"] - drawn, 3)
        self.assertEqual(fight.hand[-1].id, expected)
        self.assertEqual(len(fight.row["topdecked"]), 1)
        self.assertEqual(fight.exhausted, 1)
        self.assertEqual(fight.energy, 3)
        self.assertEqual(len(fight.hand), 4)
        self.assertEqual(fight.draw_pile, [])
        self.assertEqual(fight.discard, [])

    def test_thinking_ahead_still_returns_one_card_when_nothing_can_be_drawn(self):
        fight = self.make_fight(["深谋远虑", "防御"])
        self.play_named(fight, "深谋远虑")
        self.assertEqual(fight.hand, [])
        self.assertEqual([c.name for c in fight.draw_pile], ["防御"])
        self.assertEqual(fight.exhausted, 1)
        self.assertEqual(fight.first_trigger["深谋远虑"], 1)

    def test_ringed_deck_runs_through_both_public_pipelines(self):
        raw = request(["深谋远虑", "凶恶", "防御", "耸肩无视+", "痛击+"])
        self.assertEqual(evaluate(raw)["status"], "ok")
        self.assertEqual(fight_boss(raw, "ceremonial_beast")["status"], "ok")


class BossTests(unittest.TestCase):
    def test_boss_script_is_not_replaced_by_the_dummy_profile(self):
        from brain.sim.scenarios import encounter

        fight = _ObservedFight(parse(request(["打击"], incoming_by_turn=99)), 0, encounter("ceremonial_beast"))
        fight.start_turn()
        self.assertEqual(fight.monsters[0].script[0].handler, "gain_plow")
        fight.end_turn()
        self.assertEqual(fight.monsters[0].powers["PLOW_POWER"], 150)
        self.assertEqual(fight.hp, 80)

    def test_ringing_is_preserved_after_re_reading_state(self):
        from brain.combat.search import decide_state

        fight = _ObservedFight(parse(request(["打击", "打击"])), 0)
        fight.ringing_pending = 1
        fight.start_turn()
        fight.play(0)
        decision = decide_state(fight.to_state())
        self.assertEqual(decision.action.kind, "end_turn")

    def test_spawned_status_cards_do_not_need_initial_deck_type_entries(self):
        from brain.sim.special_intents import WOUND

        fight = _ObservedFight(parse(request(["打击"])), 0)
        fight.discard.append(WOUND)
        fight.start_turn()
        status = next(c for c in fight.to_state()["player"]["hand"] if c["id"] == "WOUND")
        self.assertFalse(status["can_play"])

    def test_boss_pipeline_reports_wins_and_uses_correct_target_ids(self):
        result = fight_boss(request([{"name": "测试多段", "cost": 1, "damage": 300, "hits": 9}], runs=2),
                           "vantom", max_turns=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"]["wins"], 2)
        self.assertTrue(all(r["hp_left"] == 80 for r in result["observations"]))

    def test_boss_command_uses_the_same_pipeline(self):
        raw = request([{"name": "测试多段", "cost": 1, "damage": 300, "hits": 9}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "brain.sim.evaluator", "--from", str(path),
                                     "--boss", "vantom", "--max-turns", "3"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["summary"]["wins"], 1)

    def test_fatal_overkill_is_not_counted_as_extra_hp_loss(self):
        result = evaluate(request([{"name": "空牌", "cost": 0}], hp=1, incoming_by_turn=10))
        self.assertEqual(result["dimensions"]["defense"]["enemy_hp_loss"][0]["mean"], 1)


if __name__ == "__main__":
    unittest.main()
