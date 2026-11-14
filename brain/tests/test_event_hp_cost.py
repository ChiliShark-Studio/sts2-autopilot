"""血量低时不该再用生命换事件收益.

复盘 (2026-11-14 第 12 层巨大花卉): 41/86 血时连选三次付血分支
(深入探索 −5、深入探索 −6、抵达核心 −7), 血量 41 -> 23, 只为换一个抽牌遗物。
提示词里写了"血量高于六成才用生命换上限", 但模型用"战后能回血"说服了自己。
所以这条规则改成代码过滤: 血量低于六成时, 付血选项直接从候选里去掉。
"""

from __future__ import annotations

import unittest

from brain.build.flow import _hp_cost, BuildFlowTool
from brain.contracts import DecisionKind, DecisionRequest
from brain.model import GameState

FLOWER = {
    "state_type": "event",
    "run": {"act": 1, "floor": 12, "ascension": 10},
    "event": {
        "event_id": "COLOSSAL_FLOWER", "event_name": "巨大花卉",
        "options": [
            {"index": 0, "title": "采集花蜜", "description": "获得35金币。"},
            {"index": 1, "title": "深入探索", "description": "深入。失去5点生命值"},
        ],
    },
    "player": {"character": "铁甲战士", "hp": 41, "max_hp": 86, "gold": 205, "deck": []},
}


def request(hp: int) -> DecisionRequest:
    raw = dict(FLOWER)
    raw["player"] = {**FLOWER["player"], "hp": hp}
    return DecisionRequest(kind=DecisionKind.BUILD, state=GameState.parse(raw))


class HpCostTests(unittest.TestCase):
    def test_cost_is_read_from_description(self):
        self.assertEqual(_hp_cost(FLOWER["event"]["options"][1]), 5)
        self.assertEqual(_hp_cost(FLOWER["event"]["options"][0]), 0)

    def test_blood_costs_are_dropped_when_weak(self):
        state = request(hp=30).state
        kept = BuildFlowTool()._drop_blood_costs(state, FLOWER["event"]["options"])
        self.assertEqual([o["index"] for o in kept], [0])

    def test_blood_costs_are_kept_when_healthy(self):
        state = request(hp=70).state
        kept = BuildFlowTool()._drop_blood_costs(state, FLOWER["event"]["options"])
        self.assertEqual([o["index"] for o in kept], [0, 1])

    def test_all_options_cost_blood_keeps_them(self):
        options = [{"index": 0, "title": "A", "description": "失去5点生命值。"}]
        state = request(hp=20).state
        kept = BuildFlowTool()._drop_blood_costs(state, options)
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
