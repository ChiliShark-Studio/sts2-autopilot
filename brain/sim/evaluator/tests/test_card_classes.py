"""卡牌子类、共享效果及两个调用方的一致性；测试仍放独立工具目录。"""

import copy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from brain.cards import (BaseCard, CardType, DemonForm, Effect, EffectKind, Event, Rage, TriggeredEffect,
                         find_card, get_card)
from brain.combat.effects import apply_effects, emit, power_amount, power_statuses, read_powers
from brain.model import GameState
from brain.sim.engine import Fight, Intent, Monster
from brain.sim.evaluator import evaluate
from brain.sim.evaluator.core import _ObservedFight
from brain.sim.evaluator.input import parse
from brain.sim.deck import card
from brain.combat.search import _Sim


def values():
    return SimpleNamespace(strength=0, block=0, effect_powers={})


def play_named(fight, name):
    return fight.play(next(i for i, c in enumerate(fight.hand) if c.name == name))


class CardClassTests(unittest.TestCase):
    def test_base_is_abstract_and_registry_creates_specific_subclasses(self):
        with self.assertRaises(TypeError):
            BaseCard()
        self.assertIsInstance(get_card("RAGE"), Rage)
        self.assertIsInstance(get_card("DEMON_FORM"), DemonForm)
        self.assertEqual(get_card("RAGE").type, CardType.SKILL)
        self.assertEqual(get_card("DEMON_FORM").type, CardType.POWER)

    def test_upgrades_use_the_same_class_with_different_effect_amounts(self):
        for identity, event, kind, amount in (
                ("RAGE", Event.ATTACK_RESOLVED, EffectKind.GAIN_BLOCK, 5),
                ("DEMON_FORM", Event.TURN_START, EffectKind.GAIN_STRENGTH, 3)):
            base, upgraded = get_card(identity), get_card(identity, upgraded=True)
            self.assertIs(type(base), type(upgraded))
            self.assertEqual(upgraded.amount(event, kind), amount)
            self.assertTrue(upgraded.name.endswith("+"))

    def test_name_lookup_is_only_an_input_alias_and_preserves_upgrades(self):
        self.assertEqual(find_card("狂怒+"), get_card("RAGE", upgraded=True))
        self.assertEqual(find_card("恶魔形态", upgraded=True), get_card("DEMON_FORM", upgraded=True))
        with self.assertRaises(KeyError):
            get_card("狂怒")
        with self.assertRaises(KeyError):
            get_card("UNKNOWN")
        self.assertIsNone(find_card("未知牌"))

    def test_static_card_definitions_cannot_be_modified(self):
        rage = get_card("RAGE")
        with self.assertRaises(FrozenInstanceError):
            rage.cost = 99
        with self.assertRaises(FrozenInstanceError):
            rage.upgraded = True

    def test_rage_executes_shared_status_and_expires_at_turn_end(self):
        state = values()
        get_card("RAGE").play(state)
        self.assertEqual(state.block, 0)
        emit(state, Event.ATTACK_RESOLVED)
        emit(state, Event.ATTACK_RESOLVED)
        self.assertEqual(state.block, 6)
        emit(state, Event.TURN_END)
        self.assertEqual(state.effect_powers, {})
        emit(state, Event.ATTACK_RESOLVED)
        self.assertEqual(state.block, 6)

    def test_demon_form_only_grants_strength_at_turn_start_and_persists(self):
        state = values()
        get_card("DEMON_FORM").play(state)
        self.assertEqual(state.strength, 0)
        emit(state, Event.ATTACK_RESOLVED)
        self.assertEqual(state.strength, 0)
        emit(state, Event.TURN_START)
        emit(state, Event.TURN_END)
        emit(state, Event.TURN_START)
        self.assertEqual(state.strength, 4)
        self.assertEqual(power_amount(state, "DEMON_FORM_POWER"), 2)

    def test_powers_stack_and_state_is_not_stored_in_card_objects(self):
        a, b = values(), values()
        rage = get_card("RAGE")
        rage.play(a, multiplier=2)
        rage.play(b)
        emit(a, Event.ATTACK_RESOLVED)
        emit(b, Event.ATTACK_RESOLVED)
        self.assertEqual((a.block, b.block), (6, 3))
        clone = copy.deepcopy(a)
        emit(clone, Event.TURN_END)
        self.assertEqual(power_amount(a, "RAGE_POWER"), 6)

    def test_unknown_effect_or_status_is_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            apply_effects(values(), (TriggeredEffect("TEST_POWER", "测试状态", Event.TURN_START, "unknown", 1),))
        with self.assertRaises(ValueError):
            apply_effects(values(), (Effect("unknown", 1),))

    def test_entire_trigger_rule_is_owned_by_the_card_class(self):
        class OtherRage(Rage):
            @property
            def effects(self):
                return (TriggeredEffect("TEST_POWER", "测试状态", Event.TURN_START,
                                        EffectKind.GAIN_BLOCK, 4, Event.TURN_END),)

        state = values()
        OtherRage().play(state)
        emit(state, Event.ATTACK_RESOLVED)
        self.assertEqual(state.block, 0)
        emit(state, Event.TURN_START)
        self.assertEqual(state.block, 4)
        emit(state, Event.TURN_END)
        self.assertEqual(state.effect_powers, {})

    def test_active_instance_keeps_rule_and_amount_after_source_card_is_gone(self):
        state = values()
        source = get_card("RAGE", upgraded=True)
        source.play(state)
        del source
        emit(state, Event.ATTACK_RESOLVED)
        self.assertEqual(state.block, 5)
        active = state.effect_powers["RAGE_POWER"]
        self.assertEqual(active.rule.expires, Event.TURN_END)
        self.assertEqual(active.amount, 5)

    def test_upgraded_and_base_instances_stack_with_the_same_trigger_rule(self):
        state = values()
        get_card("RAGE").play(state)
        get_card("RAGE", upgraded=True).play(state)
        emit(state, Event.ATTACK_RESOLVED)
        self.assertEqual(state.block, 8)

    def test_conflicting_rules_for_one_status_id_are_rejected(self):
        state = values()
        get_card("RAGE").play(state)
        other = TriggeredEffect("RAGE_POWER", "狂怒", Event.TURN_START, EffectKind.GAIN_BLOCK, 3)
        with self.assertRaises(ValueError):
            apply_effects(state, (other,))

    def test_restore_and_export_use_rules_derived_from_card_classes(self):
        state = values()
        state.effect_powers = read_powers({"RAGE_POWER": 5, "DEMON_FORM_POWER": 3})
        emit(state, Event.ATTACK_RESOLVED)
        emit(state, Event.TURN_START)
        self.assertEqual((state.block, state.strength), (5, 3))
        self.assertEqual({s["id"]: s["amount"] for s in power_statuses(state)},
                         {"RAGE_POWER": 5, "DEMON_FORM_POWER": 3})

    def test_simulator_loader_retains_the_actual_card_subclass(self):
        rage = card("RAGE", upgraded=True)
        demon = card("DEMON_FORM")
        self.assertIsInstance(rage.definition, Rage)
        self.assertIsInstance(demon.definition, DemonForm)
        self.assertEqual((rage.id, rage.name, rage.block_per_attack), ("RAGE", "狂怒+", 5))
        self.assertEqual(demon.per_turn_strength, 2)

    def test_search_native_fight_and_evaluator_use_the_same_rage_trigger(self):
        deck = [card("RAGE"), card("双重打击"), card("打击")]
        fight = Fight(deck, [Monster("测试怪", 100, 100, [Intent("wait")])])
        fight.start_turn()
        raw = fight.to_state()
        parsed = GameState.parse(raw)
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.play(next(i for i, c in enumerate(sim.hand) if c.id == "RAGE"), None)
        while sim.hand:
            sim.play(0, 0)
        play_named(fight, "狂怒")
        reread = GameState.parse(fight.to_state())
        self.assertEqual(_Sim.build(reread.player, reread.battle).rage, 3)
        play_named(fight, "双重打击")
        play_named(fight, "打击")
        observed = _ObservedFight(parse({"deck": ["狂怒", "双重打击", "打击"], "runs": 1}), 0)
        observed.start_turn()
        play_named(observed, "狂怒")
        play_named(observed, "双重打击")
        play_named(observed, "打击")
        self.assertEqual((sim.block, fight.block, observed.block), (6, 6, 6))
        self.assertEqual(observed.row["block_generated"], 6)
        fight.end_turn()
        self.assertEqual(fight.rage, 0)

    def test_native_power_leaves_the_deck_and_uses_the_shared_turn_event(self):
        fight = Fight([card("DEMON_FORM")], [Monster("测试怪", 100, 100, [Intent("wait")])])
        fight.start_turn()
        self.assertTrue(play_named(fight, "恶魔形态"))
        self.assertEqual(fight.strength, 0)
        fight.end_turn()
        fight.turn += 1
        fight.start_turn()
        self.assertEqual((fight.strength, fight.per_turn_strength), (2, 2))
        self.assertEqual(fight.hand + fight.draw_pile + fight.discard, [])

    def test_upgraded_class_capabilities_survive_native_state_conversion(self):
        for identity, field, amount in (("RAGE", "block_per_attack", 5),
                                       ("DEMON_FORM", "per_turn_strength", 3)):
            fight = Fight([card(identity, upgraded=True)], [Monster("测试怪", 100, 100, [Intent("wait")])])
            fight.start_turn()
            parsed = GameState.parse(fight.to_state())
            self.assertTrue(parsed.battle.hand[0].upgraded)
            self.assertEqual(getattr(parsed.battle.hand[0], field), amount)

    def test_card_type_controls_corruption_without_treating_powers_as_skills(self):
        fight = Fight([card("DEMON_FORM"), card("RAGE")],
                      [Monster("测试怪", 100, 100, [Intent("wait")])])
        fight.corruption = True
        fight.start_turn()
        self.assertTrue(play_named(fight, "恶魔形态"))
        self.assertEqual((fight.energy, fight.exhausted), (0, 0))
        self.assertTrue(play_named(fight, "狂怒"))
        self.assertEqual((fight.energy, fight.exhausted, fight.rage), (0, 1, 3))

    def test_migrated_cards_need_no_generated_chinese_description_to_be_recognized(self):
        fight = _ObservedFight(parse({"deck": ["RAGE", "DEMON_FORM"], "runs": 1}), 0)
        fight.start_turn()
        raw = fight.to_state()
        for entry in raw["player"]["hand"]:
            self.assertEqual(entry["description"], "")
            self.assertIn(entry["id"], {"RAGE", "DEMON_FORM"})
        parsed = GameState.parse(raw)
        cards = {c.id: c for c in parsed.battle.hand}
        self.assertEqual(cards["RAGE"].block_per_attack, 3)
        self.assertEqual(cards["DEMON_FORM"].per_turn_strength, 2)
        self.assertTrue(cards["RAGE"].effects)

    def test_explicit_dynamic_values_still_override_static_definitions_at_input_boundary(self):
        fight = _ObservedFight(parse({"deck": [{"name": "恶魔形态", "cost": 3,
                                                "per_turn_strength": 7, "type": "power"}], "runs": 1}), 0)
        fight.start_turn()
        parsed = GameState.parse(fight.to_state())
        self.assertEqual(parsed.battle.hand[0].per_turn_strength, 7)
        play_named(fight, "恶魔形态")
        self.assertEqual(fight.per_turn_strength, 7)

    def test_internal_definition_objects_are_not_accepted_as_json_input(self):
        result = evaluate({"deck": [{"name": "伪造", "definition": {"id": "RAGE"}}], "runs": 1})
        self.assertEqual(result["status"], "unsupported")
        self.assertTrue(any("definition" in s for s in result["coverage"]["missing"]))


if __name__ == "__main__":
    unittest.main()
