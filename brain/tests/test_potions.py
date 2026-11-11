"""Potion timing: only when it changes the outcome."""

from __future__ import annotations

import unittest

from brain.tools.combat_search import decide_state


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
            "hand": hand or [{"index": 0, "name": "打击", "cost": "1", "damage": 6, "hits": 1}],
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
        # 3 attacks x 6 damage; +5 strength is +15 damage -- worth a potion at 43% HP.
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
        self.assertEqual(sim.hand[0].damage, 12)  # exact, not 6+3

    def test_potion_used_when_upgrade_gain_is_large(self):
        hand = [{"index": i, "name": "重击", "cost": "1", "damage": 6,
                 "hits": 1, "target_type": "AnyEnemy", "damage_upgraded": 30}
                for i in range(3)]
        decision = decide_state(state(hp=30, enemy_hp=60, hand=hand, potions=[forge_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        # Upgrading all three makes the turn lethal, so it reports 斩杀.
        self.assertIn("熔炉的祝福", decision.reason)


class PotionTests(unittest.TestCase):
    def test_no_potion_plays_a_card(self):
        decision = decide_state(state())
        self.assertEqual(decision.action.kind, "play_card")

    def test_uses_block_potion_to_survive_lethal(self):
        # 10 HP, taking 15: without help we die.
        decision = decide_state(state(hp=10, incoming=15, potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertEqual(decision.action.slot, 0)
        self.assertIn("保命", decision.reason)

    def test_ignores_block_potion_when_survivable(self):
        decision = decide_state(state(hp=70, incoming=15, potions=[block_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_uses_damage_potion_to_finish_single_enemy(self):
        # 6 damage from hand vs 20 HP, fire potion does 20: potion finishes it.
        decision = decide_state(state(enemy_hp=20, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "use_potion")
        self.assertEqual(decision.action.target, "E0")
        self.assertIn("斩杀", decision.reason)

    def test_does_not_waste_potion_when_hand_already_lethal(self):
        decision = decide_state(state(enemy_hp=6, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_damage_potion_skipped_when_it_would_not_finish(self):
        # Hand does 6, potion 20, enemy 40: still not lethal, so save it.
        decision = decide_state(state(enemy_hp=40, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_multi_enemy_skips_finisher_logic(self):
        decision = decide_state(state(enemy_hp=10, enemies=2, potions=[fire_potion()]))
        self.assertEqual(decision.action.kind, "play_card")

    def test_unusable_potion_is_ignored(self):
        potion = dict(block_potion(), can_use_in_combat=False)
        decision = decide_state(state(hp=10, incoming=15, potions=[potion]))
        self.assertEqual(decision.action.kind, "play_card")


if __name__ == "__main__":
    unittest.main()
