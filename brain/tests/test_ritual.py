"""仪式 (RITUAL_POWER): 邪教徒回合结束固定加力量, 离线引擎也要能跑.

真机证据 (logs/run-20261114-044556.jsonl 第 12 层):
  * 钙化邪教徒 41 血, 第 1 回合念咒获得仪式 +2, 之后攻击 9;
  * 潮湿邪教徒 52 血, 仪式 +5, 之后攻击 1;
  * 潮湿邪教徒的力量一路涨到 20, 我们 5 回合掉了 44 点血。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from brain.sim.engine import Fight, Intent, Monster, DeckCard
from brain.sim.scenario_file import load


def fight(**kwargs) -> Fight:
    deck = [DeckCard(name="打击", cost=1, damage=1, target="AnyEnemy")]
    monster = Monster(name="钙化邪教徒", hp=99, max_hp=99,
                      script=[Intent("attack", 9)], ritual=2, **kwargs)
    return Fight(deck, [monster], hp=100, max_hp=100)


class RitualEngineTests(unittest.TestCase):
    def test_strength_grows_every_turn(self):
        battle = fight()
        self.assertEqual(battle.monsters[0].strength, 0)
        battle.end_turn()
        self.assertEqual(battle.monsters[0].strength, 2)
        battle.end_turn()
        self.assertEqual(battle.monsters[0].strength, 4)

    def test_attack_damage_follows_strength(self):
        battle = fight()
        battle.end_turn()          # 第一回合后 +2
        hp_before = battle.hp
        battle.end_turn()          # 第二回合攻击 9+2
        self.assertEqual(hp_before - battle.hp, 11)


class RitualScenarioTests(unittest.TestCase):
    def test_scenario_loads_ritual(self):
        scenario = load(Path("scenarios/act1-cultists-20261114.json"))
        rituals = {m.name: m.ritual for m in scenario.phases[0]}
        self.assertEqual(rituals["钙化邪教徒"], 2)
        self.assertEqual(rituals["潮湿邪教徒"], 5)


if __name__ == "__main__":
    unittest.main()
