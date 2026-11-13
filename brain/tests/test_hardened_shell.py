"""硬化外壳 (鬼祟珊瑚群) 建模测试.

真机证据: logs/run-20261114-044556.jsonl 第一幕第 11 层, 75 血的鬼祟珊瑚群带
HARDENED_SHELL_POWER 20, 说明为"每回合失去的生命值不会超过20点".
录像里额度每回合从 20 开始, 按实际掉血递减 (20 -> 12 -> 3), 回合结束重置.

这一条不建模时, 搜索会以为可以靠单回合爆发速杀, 把伤害和能量浪费在上限之外.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brain.combat.search import _Sim
from brain.model import GameState
from brain.sim import scenarios
from brain.sim.engine import DeckCard, Fight, Intent, Monster
from brain.sim.scenario_file import load


def sim(*, enemy_hp=75, shell=20, damage=6, hits=1, hand=None):
    status = [] if shell is None else [{"id": "HARDENED_SHELL_POWER", "name": "硬化外壳",
                                        "amount": shell}]
    raw = {
        "state_type": "monster",
        "battle": {
            "round": 1, "turn": "player", "ready_for_action": True,
            "enemies": [{"entity_id": "SKULKING_COLONY_0", "name": "鬼祟珊瑚群",
                         "hp": enemy_hp, "max_hp": 75,
                         "status": status,
                         "intents": [{"type": "Attack", "damage": 14, "hits": 1}]}],
        },
        "player": {"hp": 40, "max_hp": 86, "energy": 3,
                   "hand": hand or [{"index": 0, "name": "重锤", "cost": "1",
                                     "damage": damage, "hits": hits,
                                     "target_type": "AnyEnemy"}]},
    }
    state = GameState.parse(raw)
    return _Sim.build(state.player, state.battle)


class SearchShellTests(unittest.TestCase):
    def test_damage_above_the_cap_is_wasted(self):
        s = sim(damage=30)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 55)   # 只掉 20, 多出的 10 点无效
        self.assertEqual(s.enemies[0].hp_loss_left, 0)

    def test_cap_decrements_by_the_life_actually_lost(self):
        s = sim(damage=12)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 63)
        self.assertEqual(s.enemies[0].hp_loss_left, 8)

    def test_attack_after_the_cap_is_spent_deals_nothing(self):
        s = sim(damage=20, hand=[{"index": 0, "name": "重锤", "cost": "1", "damage": 20,
                                  "hits": 1, "target_type": "AnyEnemy"},
                                 {"index": 1, "name": "打击", "cost": "1", "damage": 6,
                                  "hits": 1, "target_type": "AnyEnemy"}])
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 55)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 55)   # 额度已用完, 这张牌等于白打

    def test_enemy_without_the_power_is_unaffected(self):
        s = sim(shell=None, damage=30)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 45)

    def test_exhausted_budget_with_the_power_still_blocks_damage(self):
        # 状态里 amount=0 表示额度用完, 与"没有这个能力"必须区分开.
        s = sim(shell=0, damage=30)
        s.play(0, 0)
        self.assertEqual(s.enemies[0].hp, 75)


class EngineShellTests(unittest.TestCase):
    def test_engine_caps_loss_within_a_turn_and_resets_next_turn(self):
        monster = Monster("鬼祟珊瑚群", hp=75, max_hp=75,
                          script=[Intent("attack", 0)], hp_loss_cap=20)
        fight = Fight([DeckCard("重锤", cost=0, damage=25)] * 3, [monster], seed=1)
        fight.start_turn()
        fight.play(0)
        self.assertEqual(monster.hp, 55)
        fight.play(0)
        self.assertEqual(monster.hp, 55)        # 本回合额度已用完
        fight.end_turn()
        fight.start_turn()
        fight.play(0)
        self.assertEqual(monster.hp, 35)        # 新回合额度重置

    def test_state_export_reports_remaining_budget(self):
        monster = Monster("鬼祟珊瑚群", hp=75, max_hp=75,
                          script=[Intent("attack", 0)], hp_loss_cap=20)
        fight = Fight([DeckCard("重锤", cost=0, damage=12)], [monster], seed=2)
        fight.start_turn()
        fight.play(0)
        status = fight.to_state()["battle"]["enemies"][0]["status"]
        shell = next(s for s in status if s["id"] == "HARDENED_SHELL_POWER")
        self.assertEqual(shell["amount"], 8)


class ColonyScenarioTests(unittest.TestCase):
    def test_scenario_matches_the_recorded_elite(self):
        colony = scenarios.encounter("skulking_colony")[0]
        self.assertEqual((colony.hp, colony.max_hp), (75, 75))
        self.assertEqual(colony.hp_loss_cap, 20)
        self.assertEqual(colony.hp_loss_left, 20)
        # 鬼祟珊瑚群属于暗港 (本局 Boss 是灵魂异鱼), 不属于密林.
        self.assertIn("skulking_colony", scenarios.elites_for(1, "SOUL_FYSH_BOSS"))
        self.assertNotIn("skulking_colony", scenarios.elites_for(1, "VANTOM_BOSS"))

    def test_scenario_file_maps_the_status_to_the_cap(self):
        raw = {
            "name": "鬼祟珊瑚群", "seed": 1, "max_turns": 5,
            "player": {"hp": 80, "max_hp": 80, "deck": [{"name": "打击"}]},
            "enemies": [{"entity_id": "SKULKING_COLONY_0", "name": "鬼祟珊瑚群",
                         "hp": 75, "max_hp": 75,
                         "status": [{"id": "HARDENED_SHELL_POWER", "amount": 20}],
                         "intents": [{"type": "attack", "damage": 14}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "colony.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            scenario = load(path)
        monster = scenario.phases[0][0]
        self.assertEqual(monster.hp_loss_cap, 20)
        self.assertEqual(monster.hp_loss_left, 20)


if __name__ == "__main__":
    unittest.main()
