"""战斗中的"单卡判断题"顾问: 只回答"这张牌现在打不打", 不参与其他出牌.

两类牌模型算不准, 交给模型:
  * 用生命换节奏的牌 (祭品 / 放血): 价值一半在抽到的牌上, 单回合模型没法模拟牌序;
  * 效果没被建模的能力牌: 本地模拟里就是白板 (例如新版本新增的能力),
    打不打完全取决于它的实际效果。

约束:
  * 只在战斗内、只在搜索本回合不打算打它时询问;
  * 同一场战斗同一回合只问一次, 答案缓存;
  * 模型说"留着"就尊重; 没有模型或回复无效时, 由调用方决定兜底。
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from ..actions import PlayCard
from ..contracts import Decision, Source
from ..effects import humanize
from ..llm import LLMClient
from ..model import GameState

# 模型选择"留着不打"的编号.
DECLINE = -1


class CardAdvisor:
    """单卡判断的通用实现; 具体问哪类牌由 ``matches`` 与 ``task`` 决定."""

    name = "card_advisor"

    def __init__(self, client: Optional[LLMClient] = None, *,
                 matches: Callable[[object], bool],
                 task: str) -> None:
        self.client = client
        self._matches = matches
        self._task = task
        # (战斗类型, 层数, 回合, 手牌) -> (裁决, 决策), 避免同一回合反复询问.
        self._cache: dict[tuple, tuple[str, Optional[Decision]]] = {}
        # 最近一次裁决: play (打) / keep (留着) / unavailable (无模型或回复无效).
        self.last_verdict = "unavailable"
        self.last_note = ""

    def candidates(self, state: GameState) -> list:
        battle = state.battle
        if battle is None:
            return []
        return [card for card in battle.hand if card.can_play and self._matches(card)]

    @staticmethod
    def _key(state: GameState) -> tuple:
        battle = state.battle
        hand = tuple(sorted(card.name for card in battle.hand)) if battle else ()
        return (state.screen.value, state.run.floor,
                battle.round if battle else 0, hand)

    def advise(self, state: GameState, cards: list) -> Optional[Decision]:
        """询问是否现在打出; 返回决策表示"打", None 时看 ``last_verdict``."""
        key = self._key(state)
        if key in self._cache:
            verdict, decision = self._cache[key]
            self.last_verdict, self.last_note = verdict, "本回合已询问过"
            return decision

        self.last_verdict, self.last_note = "unavailable", ""
        if self.client is None or not cards:
            self.last_note = "没有模型, 走本地规则"
            self._cache[key] = ("unavailable", None)
            return None

        battle = state.battle
        player = state.player
        payload = {
            "situation": {
                "hp": player.hp,
                "max_hp": player.max_hp,
                "block": player.block,
                "energy": player.energy,
                "hand": [card.name for card in battle.hand],
                "enemies": [
                    {"name": e.name, "hp": e.hp, "max_hp": e.max_hp, "incoming": e.incoming}
                    for e in battle.alive_enemies
                ],
                "relics": [r.get("name")
                           for r in ((state.raw.get("player") or {}).get("relics") or [])],
                "draw_pile": battle.draw_count,
                "discard_pile": battle.discard_count,
                "deck": [card.name for card in player.deck],
            },
            "candidates": [
                {"choice": index, "name": card.name, "cost": card.cost,
                 "hp_cost": card.hp_cost, "energy": card.energy, "draw": card.draw,
                 "description": card.description}
                for index, card in enumerate(cards)
            ],
            "decline": DECLINE,
        }
        system = f"""你是《杀戮尖塔2》的战斗出牌顾问, 只判断一件小事: {self._task}
- 效果以给定的 name / description / hp_cost / energy / draw 为准, 不编造数值, 不假设抽到或随机到的具体牌。
- 若当前生命很低、敌人下回合就会打死你, 或本回合能直接斩杀, 选择留着 (-1)。
- 只有当"现在打出去"确实能加快结束战斗、少掉更多血时, 才选择打。
只输出 JSON：{{"choice": <候选编号或 -1>, "reason": "<简短中文依据>"}}"""
        reply = self.client.json_chat(system, humanize(json.dumps(payload, ensure_ascii=False)))
        if not isinstance(reply, dict) or type(reply.get("choice")) is not int:
            self.last_note = "模型回复无效, 走本地规则"
            self._cache[key] = ("unavailable", None)
            return None

        choice = reply["choice"]
        reason = str(reply.get("reason") or "")[:120]
        if choice == DECLINE:
            self.last_verdict = "keep"
            self.last_note = f"模型选择留着：{reason}"
            self._cache[key] = ("keep", None)
            return None
        card = next((item for index, item in enumerate(cards) if index == choice), None)
        if card is None:
            self.last_note = f"模型给了不存在的编号 {choice}, 走本地规则"
            self._cache[key] = ("unavailable", None)
            return None

        self.last_verdict = "play"
        self.last_note = ""
        decision = Decision(
            action=PlayCard(card, None),
            reason=f"{card.name}: {reason or '模型判断值得打出'}",
            source=Source.LLM,
            tool=self.name,
            extra={"llm": dict(self.client.last_meta or {})},
        )
        self._cache[key] = ("play", decision)
        return decision
