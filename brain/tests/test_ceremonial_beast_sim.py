"""仪式兽的模拟战斗测试: 击晕/阶段切换/昏眩在整场战斗里的表现.

场景数值来自游戏内文本与社区 wiki (252 血、跺地 150 层横冲直撞、
横冲直撞 18 伤 +2 力量、二阶段咆哮-踩踏-碾碎), 属于"近似重建",
用于同种子下的策略对比, 不代表真实胜率.
"""

from __future__ import annotations

import unittest

from brain.enemy_rules import RuleBook
from brain.sim.arena import run_fight
from brain.sim.scenarios import encounter, starter
from brain.tools.combat_search import decide_state


def _deck():
    return starter("ironclad")


def _recorded(policy):
    """包一层策略, 记录每次决策时的状态, 便于断言机制是否生效."""
    log = []

    def wrapped(raw):
        decision = policy(raw)
        log.append((raw, decision))
        return decision

    return wrapped, log


def _player_status(raw):
    return {s.get("id") or s.get("name") for s in
            ((raw.get("player") or {}).get("status") or [])}


def _enemy(raw):
    return ((raw.get("battle") or {}).get("enemies") or [{}])[0]


class CeremonialBeastSimTests(unittest.TestCase):
    def test_boss_is_stunned_once_below_plow_layers(self):
        policy, log = _recorded(decide_state)
        run_fight(_deck(), encounter("ceremonial_beast"), seed=3, hp=200, max_hp=200,
                  policy=policy)
        stun_frames = [raw for raw, _ in log
                       if _enemy(raw).get("hp") is not None
                       and _enemy(raw).get("hp") <= 150
                       and any(i.get("type") == "Stun" for i in _enemy(raw).get("intents") or [])]
        self.assertTrue(stun_frames, "仪式兽应当在血量降到 150 后进入击晕")
        # 击晕时力量清零.
        strength = [s for s in _enemy(stun_frames[0]).get("status") or []
                    if s.get("id") == "STRENGTH_POWER"]
        self.assertEqual(strength, [])

    def test_ringing_limits_player_to_one_card_per_turn(self):
        policy, log = _recorded(decide_state)
        run_fight(_deck(), encounter("ceremonial_beast"), seed=3, hp=200, max_hp=200,
                  policy=policy)
        self.assertTrue(any("RINGING_POWER" in _player_status(raw) for raw, _ in log),
                        "二阶段的野兽咆哮应当给玩家上昏眩")

        checked = 0
        for i, (raw, decision) in enumerate(log[:-1]):
            if "RINGING_POWER" not in _player_status(raw):
                continue
            action = decision.action
            if action is None or action.kind != "play_card":
                continue
            checked += 1
            # 同一回合内出过一张牌之后, 下一步必须是结束回合.
            self.assertEqual(log[i + 1][1].action.kind, "end_turn",
                             "昏眩回合只能打出 1 张牌")
        self.assertGreater(checked, 0, "昏眩回合里应当至少出现过一次出牌")

    def test_rules_do_not_hurt_survival(self):
        book = RuleBook.with_defaults()

        def with_rules(raw):
            return decide_state(raw, rules=book)

        base = [run_fight(_deck(), encounter("ceremonial_beast"), seed=s, hp=80, max_hp=80)
                for s in range(10)]
        improved = [run_fight(_deck(), encounter("ceremonial_beast"), seed=s, hp=80, max_hp=80,
                              policy=with_rules)
                    for s in range(10)]
        base_turns = sum(r.turns for r in base) / len(base)
        improved_turns = sum(r.turns for r in improved) / len(improved)
        # 知道阈值后应当至少不更差: 打穿阈值换来一个免费回合和力量清零.
        self.assertGreaterEqual(improved_turns, base_turns - 0.5)


if __name__ == "__main__":
    unittest.main()
