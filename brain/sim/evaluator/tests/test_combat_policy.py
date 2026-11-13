"""第一轮战斗策略：验证效果、决策及边界，不把胜率当作结算正确性的依据。"""

import copy
import unittest

from brain.effects import parse_card
from brain.model import GameState
from brain.sim.evaluator.core import _ObservedFight
from brain.sim.evaluator.input import parse
from brain.combat.search import _Enemy, _Plan, _Sim, decide_state


RAGE = {"name": "狂怒", "type": "Skill", "cost": "0", "block": 0,
        "description": "打出此牌后，你在这个回合内每打出一张攻击牌，获得3点格挡。"}
DEMON = {"name": "恶魔形态", "type": "Power", "cost": "3",
         "description": "在你的回合开始时，获得2点力量。"}
SETUP = {"name": "预备打击", "type": "Attack", "cost": "1", "damage": 7,
         "target_type": "AnyEnemy", "description": "造成7点伤害。在本回合内获得2点力量。"}
STRIKE = {"name": "打击", "type": "Attack", "cost": "1", "damage": 6,
          "target_type": "AnyEnemy"}
DEFEND = {"name": "防御", "type": "Skill", "cost": "1", "block": 5}
EXPECT = {"name": "跃跃欲试", "type": "Skill", "cost": "2", "energy": 99,
          "description": "你的手牌中每有一张攻击牌，就获得[ironclad_energy_icon.png]。"
                         "你在本回合内不能再获得[ironclad_energy_icon.png]。"}


def state(hand, *, deck=None, energy=3, incoming=0, hp=80, enemy_hp=252, powers=()):
    return {"state_type": "monster",
            "player": {"hp": hp, "max_hp": 80, "energy": energy, "max_energy": 3,
                       "hand": [{**c, "index": i} for i, c in enumerate(hand)],
                       "deck": copy.deepcopy(deck if deck is not None else hand),
                       "status": list(powers)},
            "battle": {"turn": "player", "ready_for_action": True, "enemies": [
                {"entity_id": "E0", "hp": enemy_hp, "max_hp": enemy_hp,
                 "intents": [{"type": "Attack", "damage": incoming, "hits": 1}]}]}}


def node(raw):
    parsed = GameState.parse(raw)
    return _Sim.build(parsed.player, parsed.battle)


class CombatPolicyTests(unittest.TestCase):
    def test_delayed_and_temporary_strength_are_not_immediate_permanent_strength(self):
        for raw in (DEMON, {**DEMON, "strength": 0}):
            effect = parse_card(raw)
            self.assertEqual((effect["strength"], effect["per_turn_strength"]), (0, 2))
        effect = parse_card({**SETUP, "strength": 2})
        self.assertEqual((effect["strength"], effect["temporary_strength"]), (0, 2))
        self.assertEqual(parse_card({"description": "获得3点力量。"})["strength"], 3)

    def test_rage_is_not_immediate_block_in_text_only_input(self):
        raw = dict(RAGE)
        raw.pop("block")
        effect = parse_card(raw)
        self.assertEqual(effect["block"], 0)
        self.assertFalse(effect["grants_block"])
        self.assertEqual(effect["block_per_attack"], 3)

    def test_rage_then_two_attacks_gives_six_block(self):
        sim = node(state([RAGE, STRIKE, STRIKE]))
        sim.play(0, None)
        self.assertEqual(sim.block, 0)
        sim.play(0, 0)
        sim.play(0, 0)
        self.assertEqual(sim.block, 6)

    def test_decision_prefers_rage_before_attacks_when_block_matters(self):
        raw = state([STRIKE, STRIKE, RAGE], incoming=8, hp=30)
        action = decide_state(raw).action
        self.assertEqual(action.kind, "play_card")
        self.assertEqual(action.card.name, "狂怒")

    def test_existing_rage_is_reloaded_from_current_status(self):
        sim = node(state([STRIKE], powers=[{"id": "RAGE_POWER", "amount": 3}]))
        sim.play(0, 0)
        self.assertEqual(sim.block, 3)

    def test_multihit_rage_does_not_add_dexterity_or_apply_frail(self):
        sim = node(state([RAGE, {**STRIKE, "hits": 4}], powers=[
            {"id": "DEXTERITY_POWER", "amount": 2}, {"id": "FRAIL_POWER", "amount": 1},
            {"id": "NO_BLOCK_POWER", "amount": 1}]))
        sim.play(0, None)
        sim.play(0, 0)
        self.assertEqual(sim.block, 3)

    def test_dexterity_only_affects_cards_that_grant_block(self):
        sim = node(state([STRIKE, DEFEND], powers=[{"id": "DEXTERITY_POWER", "amount": 2}]))
        sim.play(0, 0)
        self.assertEqual(sim.block, 0)
        sim.play(0, None)
        self.assertEqual(sim.block, 7)

    def test_zero_printed_block_effect_can_still_receive_dexterity(self):
        sim = node(state([{"name": "零基础格挡", "cost": "0", "block": 0,
                           "description": "获得0点格挡。"}],
                         powers=[{"id": "DEXTERITY_POWER", "amount": 2}]))
        sim.play(0, None)
        self.assertEqual(sim.block, 2)

    def test_setup_strength_applies_after_damage_and_has_no_permanent_terminal_reward(self):
        sim = node(state([SETUP, STRIKE]))
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 252 - 7)
        self.assertEqual((sim.strength, sim.temporary_strength), (2, 2))
        without = sim.clone()
        without.strength = without.temporary_strength = 0
        self.assertEqual(sim.value(), without.value())
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 252 - 7 - 8)

    def test_current_temporary_strength_is_preserved_when_rereading_state(self):
        sim = node(state([STRIKE], powers=[{"id": "STRENGTH_POWER", "amount": 5},
                                           {"id": "SETUP_STRIKE_POWER", "amount": 2}]))
        self.assertEqual((sim.strength, sim.temporary_strength), (5, 2))
        sim.play(0, 0)
        self.assertEqual(sim.enemies[0].hp, 252 - 11)

    def test_expect_recalculates_after_an_attack_leaves_hand(self):
        sim = node(state([STRIKE, EXPECT, STRIKE, STRIKE]))
        sim.play(0, 0)
        sim.play(0, None)
        self.assertEqual(sim.energy, 2)
        self.assertTrue(sim.no_energy_gain)

    def test_expect_with_no_attacks_pays_cost_and_gains_zero(self):
        sim = node(state([EXPECT, DEFEND]))
        sim.play(0, None)
        self.assertEqual(sim.energy, 1)

    def test_expect_blocks_later_card_energy(self):
        sim = node(state([EXPECT, STRIKE, {"name": "产能", "cost": "0", "energy": 2}]))
        sim.play(0, None)
        energy = sim.energy
        sim.play(1, None)
        self.assertEqual(sim.energy, energy)

    def test_existing_energy_lock_is_reloaded(self):
        sim = node(state([{"name": "产能", "cost": "0", "energy": 2}],
                         powers=[{"id": "NO_ENERGY_GAIN_POWER", "amount": 1}]))
        sim.play(0, None)
        self.assertEqual(sim.energy, 3)

    def test_growth_estimate_matches_two_future_turns_with_two_hits_each(self):
        sim = _Sim(hp=80, max_hp=80, block=0, energy=0, hand=[], enemies=[_Enemy("E0", 60, 60)],
                   growth_strength=2, max_energy=1, deck_attack_density=1,
                   attack_hits=2, attack_damage=20, attack_cost=1)
        detail = sim.strength_future()
        self.assertEqual(detail["turns"], 2)
        self.assertEqual(detail["hits_per_turn"], 2)
        self.assertEqual(detail["estimate"], 12)
        sim.enemies[0].hp = 0
        self.assertEqual(sim.strength_future()["estimate"], 0)

    def test_growth_needs_a_known_attack_profile_and_usable_energy(self):
        sim = node(state([DEMON], deck=[]))
        sim.play(0, None)
        self.assertEqual(sim.strength_future()["estimate"], 0)
        sim = node(state([DEMON], deck=[STRIKE] * 10))
        sim.play(0, None)
        sim.max_energy = 0
        self.assertEqual(sim.strength_future()["estimate"], 0)

    def test_forecast_hits_are_limited_by_draw_density_and_energy(self):
        sim = node(state([DEMON], deck=[{**STRIKE, "hits": 4, "cost": "2"}] * 5 + [DEFEND] * 20))
        sim.play(0, None)
        self.assertLessEqual(sim.strength_future()["hits_per_turn"], 4)
        sim.max_energy = 1
        self.assertLessEqual(sim.strength_future()["hits_per_turn"], 2)

    def test_growth_can_be_selected_and_explained_in_a_long_fight(self):
        attack = {**STRIKE, "damage": 1, "hits": 4}
        decision = decide_state(state([DEMON, attack], deck=[DEMON] + [attack] * 10))
        self.assertEqual(decision.action.card.name, "恶魔形态")
        self.assertGreater(decision.extra["strength_future"]["estimate"], 0)

    def test_growth_does_not_override_lethal_or_survival(self):
        raw = state([DEMON, STRIKE], enemy_hp=6)
        self.assertEqual(decide_state(raw).action.card.name, "打击")
        huge = {**DEMON, "description": "在你的回合开始时，获得9999点力量。"}
        raw = state([huge, DEFEND], deck=[STRIKE] * 20, hp=4, incoming=6)
        self.assertEqual(decide_state(raw).action.card.name, "防御")

    def test_estimate_is_zero_when_baseline_can_finish_next_turn(self):
        sim = node(state([DEMON], deck=[STRIKE] * 10, enemy_hp=6))
        sim.play(0, None)
        self.assertEqual(_Plan.from_sim(sim, []).strength_future["estimate"], 0)

    def test_evaluator_passes_public_deck_and_current_power_state(self):
        fight = _ObservedFight(parse({"deck": ["狂怒", "跃跃欲试", "打击", "双重打击"], "runs": 1}), 0)
        fight.start_turn()
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "狂怒"))
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "跃跃欲试"))
        raw = fight.to_state()
        sim = node(raw)
        self.assertEqual(sim.rage, 3)
        self.assertTrue(sim.no_energy_gain)
        self.assertGreater(sim.attack_hits, 1)
        self.assertNotIn("draw_pile", raw["player"])

    def test_evaluator_passes_setup_strength_after_actual_play(self):
        fight = _ObservedFight(parse({"deck": ["预备打击", "打击"], "runs": 1}), 0)
        fight.start_turn()
        fight.play(next(i for i, c in enumerate(fight.hand) if c.name == "预备打击"))
        sim = node(fight.to_state())
        self.assertEqual((sim.strength, sim.temporary_strength), (2, 2))


if __name__ == "__main__":
    unittest.main()
