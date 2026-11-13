"""卡牌目录迁移回归：验证版本覆盖、造牌实例隔离和评估器的规则边界。"""

from dataclasses import FrozenInstanceError
import unittest

from brain.cards import BaseCard, CardType, find_card, get_card
from brain.cards.catalog import simulation_catalog
from brain.sim.deck import card, deck
from brain.sim.evaluator import evaluate
from brain.sim.evaluator.input import parse


class CardCatalogTests(unittest.TestCase):
    def test_old_card_kinds_have_distinct_immutable_subclasses(self):
        cards = [get_card(identity) for identity in ("STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "INFLAME")]
        self.assertTrue(all(isinstance(c, BaseCard) for c in cards))
        self.assertEqual(len({type(c) for c in cards}), 3)
        self.assertEqual([c.type for c in cards], [CardType.ATTACK, CardType.SKILL, CardType.POWER])
        with self.assertRaises(FrozenInstanceError):
            cards[0].upgraded = True

    def test_only_recorded_upgrade_versions_are_available(self):
        self.assertEqual(find_card("打击+"), get_card("STRIKE_IRONCLAD", upgraded=True))
        self.assertIsNone(find_card("头槌+"))
        with self.assertRaises(KeyError):
            get_card("HEADBUTT", upgraded=True)
        with self.assertRaises(KeyError):
            card("头槌", upgraded=True)
        with self.assertRaises(KeyError):
            card("未收录的牌")

    def test_derived_catalog_is_not_mutable_definition_storage(self):
        first = simulation_catalog()
        first["打击"]["damage"] = 999
        first.pop("防御")
        second = simulation_catalog()
        self.assertEqual(second["打击"]["damage"], 6)
        self.assertIn("防御", second)

    def test_factory_preserves_native_template_identity_and_targets(self):
        strike, defend, bash = card("打击"), card("防御"), card("痛击+")
        self.assertEqual((strike.id, strike.name, strike.target), ("打击", "打击", "AnyEnemy"))
        self.assertEqual((defend.id, defend.target), ("防御", "Self"))
        self.assertEqual((bash.name, bash.damage, bash.vulnerable), ("痛击+", 10, 3))
        self.assertEqual(card("STRIKE_IRONCLAD"), strike)

    def test_deck_factory_produces_independent_runtime_cards(self):
        cards = deck({"打击": 2, "防御": 1})
        self.assertEqual([c.name for c in cards], ["打击", "打击", "防御"])
        self.assertIsNot(cards[0], cards[1])
        cards[0].cost = 0
        cards[0].damage = 100
        self.assertEqual((cards[1].cost, cards[1].damage), (1, 6))

    def test_incomplete_legacy_mechanics_remain_unsupported_by_evaluator(self):
        report = evaluate({"deck": ["连续反弹"], "runs": 1})
        self.assertEqual(report["status"], "unsupported")
        self.assertTrue(any("连续反弹" in reason for reason in report["coverage"]["missing"]))

    def test_verified_temporary_strength_is_not_downgraded_to_old_template(self):
        spec = parse({"deck": ["预备打击"], "runs": 1})
        self.assertEqual((spec.deck[0].strength, spec.deck[0].temporary_strength), (0, 2))


if __name__ == "__main__":
    unittest.main()
