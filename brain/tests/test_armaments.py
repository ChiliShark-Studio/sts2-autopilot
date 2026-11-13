"""武装的搜索结算、实际升级选择与未来收益边界。"""

import unittest

from brain.contracts import DecisionKind, DecisionRequest
from brain.effects import parse_card
from brain.model import Card, GameState
from brain.tests.test_potions import state
from brain.combat.search import _Sim, _plan, decide_state
from brain.tools.select_tool import SelectTool
from brain.upgrades import choose_upgrade, upgrade


def armaments(index=0, upgraded=False):
    return {"index": index, "id": "ARMAMENTS", "name": "武装+" if upgraded else "武装",
            "cost": "1", "block": 5, "type": "Skill", "target_type": "Self",
            "is_upgraded": upgraded,
            "description": "获得5点格挡。 升级你手牌中的所有牌。" if upgraded else
                           "获得5点格挡。 升级你手牌中的一张牌。"}


def strike(index=1, **kwargs):
    return {"index": index, "name": "打击", "type": "Attack", "cost": "1", "damage": 6,
            "target_type": "AnyEnemy", **kwargs}


def defend(index=2, **kwargs):
    return {"index": index, "name": "防御", "type": "Skill", "cost": "1", "block": 5,
            "target_type": "Self", **kwargs}


def sim(raw):
    parsed = GameState.parse(raw)
    return _Sim.build(parsed.player, parsed.battle)


class UpgradeRuleTests(unittest.TestCase):
    def test_parses_single_and_all_hand_upgrade(self):
        single, all_cards = parse_card(armaments()), parse_card(armaments(upgraded=True))
        self.assertTrue(single["upgrade_hand"])
        self.assertFalse(single["upgrade_all"])
        self.assertTrue(all_cards["upgrade_all"])

    def test_known_upgrades_change_all_verified_effects(self):
        bash = upgrade(Card(name="痛击", cost=2, damage=8, vulnerable=2))
        self.assertEqual((bash.damage, bash.vulnerable), (10, 3))
        improved = upgrade(Card.parse(armaments()))
        self.assertEqual(improved.block, 5)
        self.assertTrue(improved.upgrade_all)

    def test_preserves_existing_bonus_and_clears_stale_preview(self):
        card = Card(name="打击", cost=0, damage=8, damage_by_target={"E0": 12})
        improved = upgrade(card)
        self.assertEqual((improved.cost, improved.damage), (0, 11))
        self.assertEqual(improved.damage_by_target, {})

    def test_bridge_upgrade_value_takes_priority(self):
        self.assertEqual(upgrade(Card(name="打击", damage=6, damage_up=15)).damage, 15)

    def test_unknown_and_already_upgraded_cards_are_unchanged(self):
        for card in (Card(name="未知攻击", damage=6), Card(name="打击+", damage=9),
                     Card(name="打击", damage=9, upgraded=True)):
            self.assertIs(upgrade(card), card)

    def test_selection_uses_delta_instead_of_existing_card_strength(self):
        cards = [Card(name="大攻击", damage=50, damage_up=51), Card(name="打击", damage=6)]
        self.assertEqual(choose_upgrade(cards, .4), 1)


class ArmamentsSearchTests(unittest.TestCase):
    def test_upgraded_attack_enables_current_turn_lethal(self):
        raw = state(hp=1, incoming=30, enemy_hp=9, energy=2,
                    hand=[armaments(), strike()])
        decision = decide_state(raw)
        self.assertEqual(decision.action.card.name, "武装")
        self.assertIn("斩杀", decision.reason)
        node = sim(raw)
        node.play(0, None)
        self.assertEqual(node.hand[0].damage, 9)
        node.play(0, 0)
        self.assertEqual(node.enemies[0].hp, 0)

    def test_upgrade_block_enables_survival(self):
        raw = state(hp=1, max_hp=80, incoming=13, enemy_hp=200, energy=2,
                    hand=[armaments(), strike(), defend()])
        plan = _plan(sim(raw))
        self.assertGreater(plan.hp_left, 0)
        self.assertEqual(decide_state(raw).action.card.name, "武装")

    def test_upgraded_armaments_changes_all_remaining_cards(self):
        node = sim(state(hand=[armaments(upgraded=True), strike(), defend()]))
        node.play(0, None)
        self.assertEqual([(c.damage, c.block) for c in node.hand], [(9, 0), (0, 8)])

    def test_unknown_targets_have_no_fabricated_upgrade_bonus(self):
        node = sim(state(hand=[armaments(), strike(name="未知牌")]))
        node.play(0, None)
        self.assertEqual(node.hand[0].damage, 6)
        self.assertFalse(node.upgrade_pairs)

    def test_known_empty_targets_do_not_receive_repeated_upgrades(self):
        node = sim(state(energy=3, hand=[armaments(upgraded=True), armaments(upgraded=True),
                                       strike(name="打击+", damage=9, is_upgraded=True)]))
        node.play(0, None)
        node.play(0, None)
        self.assertEqual(node.hand[0].damage, 9)
        self.assertFalse(node.upgrade_pairs)

    def test_search_and_live_selection_choose_the_same_upgrade(self):
        raw = state(hp=1, max_hp=80, incoming=13, enemy_hp=200, energy=3,
                    hand=[armaments(), strike(), defend()])
        node = sim(raw)
        node.play(0, None)
        self.assertTrue(next(c for c in node.hand if c.name == "防御+").upgraded)
        picker = {"state_type": "hand_select", "player": raw["player"], "battle": raw["battle"],
                  "hand_select": {"prompt": "选择1张牌来升级。", "cards": [strike(), defend()]}}
        chosen = SelectTool().decide(DecisionRequest(kind=DecisionKind.SELECT, state=GameState.parse(picker)))
        self.assertEqual(chosen.action.index, 2)

    def test_survival_cannot_be_outweighed_by_large_future_upgrade_value(self):
        raw = state(hp=1, max_hp=80, incoming=20, enemy_hp=100000, energy=3,
                    hand=[armaments(), strike(cost="3", damage_upgraded=10000),
                          defend(name="强防御", cost="3", block=20)])
        raw["player"].update(deck=[{}] * 3, draw_pile_count=0, discard_pile_count=0)
        decision = decide_state(raw)
        self.assertEqual(decision.action.card.name, "强防御")

    def test_future_gain_is_bounded_and_does_not_duplicate_current_damage(self):
        raw = state(enemy_hp=200, hand=[armaments(), strike()])
        raw["player"].update(deck=[{}] * 12, draw_pile_count=10, discard_pile_count=0)
        node = sim(raw)
        node.play(0, None)
        node.play(0, 0)
        self.assertEqual(node.enemies[0].hp, 191)
        self.assertAlmostEqual(node.upgrade_future()["redraw_probability"], 10 / 12)
        self.assertEqual(node.upgrade_future()["estimate"], 2.5)
        node.enemies[0].hp = 20
        self.assertEqual(node.upgrade_future()["estimate"], 0)

    def test_missing_pile_data_does_not_promise_future_redraw(self):
        raw = state(enemy_hp=200, hand=[armaments(), strike()])
        raw["player"]["deck"] = [{}] * 12
        node = sim(raw)
        node.play(0, None)
        self.assertEqual(node.upgrade_future()["estimate"], 0)

    def test_future_estimate_is_written_to_decision_details(self):
        raw = state(incoming=10, enemy_hp=200, hand=[armaments(), strike(), defend()])
        raw["player"].update(deck=[{}] * 3, draw_pile_count=0, discard_pile_count=0)
        decision = decide_state(raw)
        self.assertIn("estimate", decision.extra["upgrade_future"])
        self.assertLessEqual(decision.extra["upgrade_future"]["redraw_probability"], 1)


if __name__ == "__main__":
    unittest.main()
