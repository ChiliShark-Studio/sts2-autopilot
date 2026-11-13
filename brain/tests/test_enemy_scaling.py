"""敌人变强的判定: 必须按怪物单独建模, 不能拿"力量层数"一概而论.

真机复盘 (2026-11-14 第一幕第 8 层): 闪光贾克斯果每回合攻击 3 并叠 2 点力量,
力量涨到 18, 但逐回合掉血是 1/14/7/1/7/16/7/12/4 (9 回合共 69 点)——
它的力量根本没加到攻击上。之前按"力量越高打越疼"建模, 模拟里第九回合会打 21 点,
严重高估; 又因为"有 Buff 意图就抢输出", 结果该防的时候也不防了。

现在: 每个怪物是否"真的在变强"写在 ``enemy_rules`` 里 (像仪式兽那样一条条写),
未知怪物不假设它会变强。
"""

from __future__ import annotations

import unittest

from brain.combat.enemy_rules import RuleBook, ShimmeringJaxfruitRule, attack_growth
from brain.combat.search import _Sim, decide_state
from brain.model import GameState

STRIKE = {"name": "打击", "cost": "1", "damage": 6, "hits": 1,
          "target_type": "AnyEnemy", "can_play": True}
DEFEND = {"name": "防御", "cost": "1", "block": 5, "hits": 1,
          "target_type": "Self", "can_play": True}


def state(*, hand, name="闪光贾克斯果", strength=0, buff=True, hp=70, energy=3,
          enemy_hp=33, incoming=3):
    intents = [{"type": "Attack", "damage": incoming, "hits": 1}]
    if buff:
        intents.append({"type": "Buff"})
    status = [{"id": "STRENGTH_POWER", "name": "力量", "amount": strength}] if strength else []
    return {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "E0", "name": name, "hp": enemy_hp,
                         "max_hp": enemy_hp, "status": status, "intents": intents}],
        },
        "player": {"hp": hp, "max_hp": 87, "energy": energy, "status": [], "hand": hand},
    }


class BuffGrowthTests(unittest.TestCase):
    def test_jaxfruit_buff_does_not_raise_damage(self):
        # 证据: 力量 2→18, 攻击始终 3 点.
        self.assertEqual(attack_growth("闪光贾克斯果", "E0", buff_intent=True), 0)

    def test_unknown_monster_is_not_assumed_to_grow(self):
        self.assertEqual(attack_growth("没见过的怪", "E0", buff_intent=True), 0)

    def test_no_growth_without_buff_intent(self):
        self.assertEqual(attack_growth("闪光贾克斯果", "E0", buff_intent=False), 0)

    def test_rule_is_registered(self):
        book = RuleBook.with_defaults()
        self.assertIsInstance(book.rule_for(type("E", (), {"name": "闪光贾克斯果",
                                                           "entity_id": "E0"})()),
                              ShimmeringJaxfruitRule)


class ScalingTests(unittest.TestCase):
    def test_jaxfruit_is_not_treated_as_scaling(self):
        parsed = GameState.parse(state(hand=[dict(DEFEND, index=0)], strength=18))
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertFalse(sim.enemies[0].scaling)
        self.assertFalse(sim.race_needed())

    def test_it_still_blocks_against_jaxfruit(self):
        # 不该因为对面有 Buff 意图就只打输出: 先挡住当回合的伤害.
        parsed = GameState.parse(state(hand=[dict(DEFEND, index=0), dict(STRIKE, index=1)],
                                       strength=18))
        sim = _Sim.build(parsed.player, parsed.battle)
        self.assertFalse(sim.race_needed())
        decision = decide_state(state(hand=[dict(DEFEND, index=0), dict(STRIKE, index=1)],
                                      strength=18))
        self.assertEqual(decision.action.card.name, "防御")

    def test_scaling_enemy_still_triggers_race(self):
        # 真会变强的敌人 (规则里 attack_growth > 0) 仍然要抢输出.
        parsed = GameState.parse(state(hand=[dict(DEFEND, index=0), dict(STRIKE, index=1)]))
        sim = _Sim.build(parsed.player, parsed.battle)
        sim.enemies[0].attack_growth = 2      # 模拟"规则说它会变强"
        self.assertTrue(sim.enemies[0].scaling)
        self.assertTrue(sim.race_needed())


if __name__ == "__main__":
    unittest.main()
