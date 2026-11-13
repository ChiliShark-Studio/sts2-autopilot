"""社区完整牌表的结算回归；不以胜率高低断言规则正确。"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brain.actions import EndTurn, PlayCard
from brain.model import Card
from brain.sim.engine import Intent, Monster
from brain.sim.evaluator import evaluate, fight_boss
from brain.sim.evaluator.core import _ObservedFight
from brain.sim.evaluator.input import parse


ROOT = Path(__file__).resolve().parents[1]


class Strength25Tests(unittest.TestCase):
    def make_fight(self, deck, **kwargs):
        spec = parse({"deck": deck, "runs": 1, "turns": 5, "incoming_by_turn": 0, **kwargs})
        self.assertEqual(spec.missing, [])
        fight = _ObservedFight(spec, 0)
        fight.start_turn()
        return fight

    def play(self, fight, name):
        self.assertTrue(fight.play(next(i for i, c in enumerate(fight.hand) if c.name == name)))

    def next_turn(self, fight):
        fight.end_turn()
        fight.turn += 1
        fight.start_turn()

    def test_full_deck_preserves_shared_card_counts_and_upgrades(self):
        source = json.loads((ROOT / "tests/strength25_source.json").read_text())
        raw = json.loads((ROOT / "community_strength25.json").read_text())
        names = {c["id"]: c["name"] for c in source["cards"]}
        self.assertEqual(raw["deck"], [names[cid] for cid in source["card_ids"]])
        self.assertEqual(len(raw["deck"]), 25)
        self.assertEqual(source["upgraded_indices"], [])
        spec = parse(raw)
        self.assertEqual(spec.missing, [])
        self.assertEqual(sum(c.strike for c in spec.deck), 12)
        self.assertEqual(sum(spec.types[c.id] == "power" for c in spec.deck), 6)

    def test_perfected_strike_counts_itself_and_exhausted_strikes(self):
        fight = self.make_fight(["完美打击", {"name": "打击", "damage": 6, "exhaust": True}], base_energy=5)
        self.play(fight, "打击")
        before = fight.monsters[0].hp
        self.play(fight, "完美打击")
        self.assertEqual(before - fight.monsters[0].hp, 10)
        self.assertEqual(fight.exhausted, 1)

    def test_full_deck_perfected_strike_preview_is_thirty(self):
        raw = json.loads((ROOT / "community_strength25.json").read_text())
        fight = self.make_fight(raw["deck"], draw_by_turn=25)
        all_cards = fight.hand + fight.draw_pile
        card = next(c for c in all_cards if c.name == "完美打击")
        fight.hand = [card]
        entry = fight.to_state()["player"]["hand"][0]
        self.assertEqual(entry["damage"], 30)
        self.assertEqual(entry["damage_by_target"], {"E0": 30})
        self.play(fight, "完美打击")
        self.assertEqual(fight.row["damage"], 30)

    def test_setup_strength_starts_after_its_damage_and_expires(self):
        fight = self.make_fight(["预备打击", "打击", "燃烧"], base_energy=5)
        self.play(fight, "燃烧")
        self.play(fight, "预备打击")
        self.assertEqual(fight.row["damage"], 9)
        self.assertEqual(fight.strength, 4)
        self.play(fight, "打击")
        self.assertEqual(fight.row["damage"], 19)
        self.next_turn(fight)
        self.assertEqual(fight.strength, 2)

    def test_prowess_grants_permanent_strength_and_dexterity(self):
        fight = self.make_fight(["非凡技艺", "防御", "打击"])
        self.play(fight, "非凡技艺")
        self.play(fight, "防御")
        self.play(fight, "打击")
        self.assertEqual((fight.block, fight.row["damage"]), (6, 7))
        self.next_turn(fight)
        self.assertEqual((fight.strength, fight.dexterity), (1, 1))
        self.assertNotIn("非凡技艺", [c.name for c in fight.hand])

    def test_demon_form_waits_until_next_turn_and_accumulates(self):
        fight = self.make_fight(["恶魔形态"])
        self.play(fight, "恶魔形态")
        self.assertEqual(fight.strength, 0)
        self.next_turn(fight)
        self.assertEqual(fight.strength, 2)
        self.assertEqual(fight.first_trigger["恶魔形态"], 2)
        self.next_turn(fight)
        self.assertEqual(fight.strength, 4)

    def test_pyre_adds_energy_on_later_turns_without_counting_it_as_external(self):
        fight = self.make_fight(["薪火之源"])
        self.play(fight, "薪火之源")
        self.assertEqual(fight.energy, 1)
        self.assertEqual(fight.row["energy_generated"], 0)
        self.next_turn(fight)
        self.assertEqual(fight.energy, 4)
        self.assertEqual(fight.row["energy_start"], 3)
        self.assertEqual(fight.row["energy_generated"], 1)
        self.assertEqual(fight.to_state()["player"]["max_energy"], 3)
        self.next_turn(fight)
        self.assertEqual(fight.energy, 4)

    def test_expect_a_fight_counts_only_current_hand_attacks_and_blocks_later_gains(self):
        fight = self.make_fight(["跃跃欲试", "打击", "双重打击", "防御",
                                 {"name": "产能", "cost": 0, "energy": 2}])
        entry = next(c for c in fight.to_state()["player"]["hand"] if c["name"] == "跃跃欲试")
        self.assertEqual(entry["energy"], 2)
        self.play(fight, "跃跃欲试")
        self.assertEqual(fight.energy, 3)
        self.assertTrue(fight.no_energy_gain)
        self.assertEqual(fight.first_trigger["跃跃欲试"], 1)
        entry = next(c for c in fight.to_state()["player"]["hand"] if c["name"] == "产能")
        self.assertEqual(entry["energy"], 0)
        self.play(fight, "产能")
        self.assertEqual(fight.energy, 3)
        self.assertEqual(fight.row["energy_generated"], 2)
        self.next_turn(fight)
        self.assertFalse(fight.no_energy_gain)

    def test_expect_a_fight_cannot_generate_energy_without_attacks(self):
        fight = self.make_fight(["跃跃欲试", "防御"])
        self.play(fight, "跃跃欲试")
        self.assertEqual(fight.energy, 1)
        self.assertEqual(fight.row["energy_generated"], 0)

    def test_pyre_and_carried_energy_are_each_added_once(self):
        fight = self.make_fight(["薪火之源"], relics=[
            {"name": "保留能量", "effects": {"carry_energy": True}}])
        self.play(fight, "薪火之源")
        self.next_turn(fight)
        self.assertEqual(fight.energy, 5)
        self.assertEqual(fight.row["energy_start"], 4)
        self.assertEqual(fight.row["energy_generated"], 1)

    def test_hellraiser_autoplays_for_free_and_nested_damage_is_counted_once(self):
        fight = self.make_fight(["地狱狂徒", "剑柄打击", "打击"], base_energy=2)
        self.play(fight, "地狱狂徒")
        cards = fight.hand
        fight.hand = []
        fight.draw_pile = sorted(cards, key=lambda c: c.name == "剑柄打击")
        fight._draw(1)
        self.assertEqual(fight.energy, 0)
        self.assertEqual(fight.row["damage"], 15)
        self.assertEqual(len(fight.row["auto_played"]), 2)
        self.assertEqual(fight.row["energy_spent"], 2)
        self.assertEqual(fight.row["drawn"], 5)
        self.assertEqual(fight.turn_cards_played, 3)
        self.assertTrue(all(c.cost == 1 for c in fight.discard))
        self.assertEqual(fight.first_trigger["地狱狂徒"], 1)

    def test_autoplay_consumes_the_requested_draw_count(self):
        fight = self.make_fight([{"name": "打击", "damage": 6, "count": 6}, "防御"], draw_by_turn=0)
        fight.hellraiser = True
        fight.draw_pile.sort(key=lambda c: c.name == "打击")
        fight._draw(5)
        self.assertEqual(fight.row["drawn"], 5)
        self.assertEqual(len(fight.row["auto_played"]), 5)
        self.assertEqual(fight.row["damage"], 30)
        self.assertEqual(len(fight.draw_pile), 2)

    def test_nested_autoplay_rage_block_is_counted_once_per_attack(self):
        fight = self.make_fight(["狂怒", "地狱狂徒", "剑柄打击", "打击"])
        self.play(fight, "狂怒")
        self.play(fight, "地狱狂徒")
        strike = next(c for c in fight.hand if c.name == "打击")
        fight.hand.remove(strike)
        fight.draw_pile = [strike]
        self.play(fight, "剑柄打击")
        self.assertEqual(fight.row["damage"], 15)
        self.assertEqual(fight.block, 6)
        self.assertEqual(fight.row["block_generated"], 6)
        self.assertEqual(fight.row["energy_spent"], 3)

    def test_ringing_blocks_further_automatic_cards_and_discards_them(self):
        fight = self.make_fight(["打击", "双重打击"], draw_by_turn=0)
        fight.hellraiser = True
        fight.ringing_active = True
        fight._draw(2)
        self.assertEqual(len(fight.row["auto_played"]), 1)
        self.assertEqual(len(fight.row["auto_blocked"]), 1)
        self.assertEqual(len(fight.discard), 2)
        self.assertEqual(fight.energy, 3)
        self.assertEqual(fight.turn_cards_played, 1)

    def test_rage_is_per_attack_not_per_hit_and_ignores_dexterity(self):
        fight = self.make_fight(["狂怒", "双重打击", "非凡技艺"], base_energy=5)
        self.play(fight, "非凡技艺")
        self.play(fight, "狂怒")
        self.play(fight, "双重打击")
        self.assertEqual(fight.block, 3)
        self.assertEqual(fight.row["block_generated"], 3)
        self.assertEqual(fight.first_trigger["狂怒"], 1)
        self.next_turn(fight)
        self.assertEqual(fight.rage, 0)

    def test_panache_excludes_itself_and_resets_each_turn(self):
        fight = self.make_fight(["神气制胜", {"name": "空牌", "cost": 0, "count": 5}], draw_by_turn=6)
        self.play(fight, "神气制胜")
        for _ in range(4):
            self.play(fight, "空牌")
        self.assertEqual(fight.row["damage"], 0)
        self.play(fight, "空牌")
        self.assertEqual(fight.row["damage"], 10)
        self.assertEqual(fight.first_trigger["神气制胜"], 1)
        self.next_turn(fight)
        self.play(fight, "空牌")
        self.assertEqual(fight.row["damage"], 0)

    def test_panache_power_damage_is_not_multiplied_by_strength_or_vulnerability(self):
        fight = self.make_fight(["神气制胜", {"name": "空牌", "cost": 0, "count": 5}], draw_by_turn=6)
        fight.strength = 50
        fight.monsters[0].vulnerable = 3
        self.play(fight, "神气制胜")
        for _ in range(5):
            self.play(fight, "空牌")
        self.assertEqual(fight.row["damage"], 10)

    def test_conflagration_scales_each_hit_and_refuses_multiple_targets(self):
        fight = self.make_fight(["焚烧"])
        fight.strength = 2
        self.play(fight, "焚烧")
        self.assertEqual(fight.row["damage"], 16)
        with self.assertRaises(ValueError):
            _ObservedFight(parse({"deck": ["焚烧"]}), 0, [
                Monster("甲", 30, 30, [Intent()]), Monster("乙", 30, 30, [Intent()])])

    def test_turn_start_automatic_lethal_is_a_complete_sample(self):
        raw = {"deck": ["地狱狂徒", {"name": "打击", "cost": 1, "damage": 100}],
               "turns": 2, "runs": 1, "enemy_hp": 80, "incoming_by_turn": 0}

        def policy(state):
            hand = state["player"]["hand"]
            power = next((c for c in hand if c["name"] == "地狱狂徒"), None)
            return SimpleNamespace(action=PlayCard(Card.parse(power)) if power else EndTurn())

        with patch("brain.sim.evaluator.core.decide_state", side_effect=policy):
            report = evaluate(raw)
        self.assertEqual(report["outcomes"]["win"], 1)
        self.assertEqual(report["outcomes"]["incomplete"], 0)
        self.assertEqual(report["dimensions"]["damage"]["by_turn"][1]["mean"], 80)

    def test_public_boss_report_includes_final_turn_and_activation_observations(self):
        raw = {"deck": [{"name": "打击", "cost": 1, "damage": 300}], "runs": 1}
        report = fight_boss(raw, "ceremonial_beast")
        self.assertEqual(report["summary"]["wins"], 1)
        self.assertEqual(report["observations"][0]["automatic_plays"], 0)


if __name__ == "__main__":
    unittest.main()
