"""战斗内药水顾问: 只处理本地无法模拟的药水效果.

抽牌药水、随机无色牌这类效果取决于牌堆顺序与随机结果, 桥接下载牌堆是按稀有度
排序输出的, 无法还原真实抽牌顺序, 因此本地不编造收益数值.
当战斗方案会死且身上带着这类药水时, 交给模型判断是否使用;
其余情况战斗仍然完全由搜索算法处理, 不调用模型.
"""

from __future__ import annotations

import json
from typing import Optional

from ..actions import UsePotion
from ..contracts import Decision, Source
from ..effects import humanize
from ..llm import LLMClient
from ..model import GameState
from .strategy import StrategyContext, situation

# 模型选择"不使用"时返回的编号.
DECLINE = -1


class PotionAdvisor:
    name = "potion_advisor"

    def __init__(self, client: Optional[LLMClient] = None,
                 context: Optional[StrategyContext] = None) -> None:
        self.client = client
        self.context = context if context is not None else StrategyContext()
        # 模型选择不使用的原因, 供战斗日志记录.
        self.last_decline = ""

    def advise(self, state: GameState, potions) -> Optional[Decision]:
        """询问模型是否使用这些药水; 返回 None 表示不使用或无法判断."""
        self.last_decline = ""
        if self.client is None or not potions:
            self.last_decline = "未配置模型"
            return None

        player = state.player
        battle = state.battle
        payload = {
            "situation": {
                "run": state.raw.get("run"),
                "player": {
                    "character": player.character,
                    "hp": player.hp,
                    "max_hp": player.max_hp,
                    "block": player.block,
                    "energy": player.energy,
                    "hand": [c.name for c in battle.hand] if battle else [],
                    "relics": [r for r in (state.raw.get("player") or {}).get("relics") or []],
                    # 只列出待判断的药水, 其余药水已由搜索评估过,
                    # 列进来会让模型去选一个不存在的候选.
                    "potions": [
                        {"slot": p.slot, "name": p.name, "description": p.description}
                        for p in potions
                    ],
                },
                "battle": {
                    "round": battle.round if battle else 0,
                    "enemies": [
                        {"name": e.name, "hp": e.hp, "max_hp": e.max_hp,
                         "incoming": e.incoming}
                        for e in (battle.alive_enemies if battle else ())
                    ],
                },
            },
            "unmodelled": [
                {"choice": p.slot, "name": p.name, "description": p.description}
                for p in potions
            ],
            "decline": DECLINE,
        }
        system = f"""你是《杀戮尖塔2》的战斗药水顾问。当前局面下, 战斗搜索算法判断本回合若不使用这些药水将会死亡。
下列药水的效果无法被模拟 (例如抽牌、随机生成牌), 因此需要你判断是否值得使用:
- 只能在这些药水的 slot 或 {DECLINE} (不使用) 中选择.
- 效果以给定描述为准, 不编造数值, 不假设抽到或随机到的具体牌.
- 若药水大概无法改变本回合的死亡结果, 选择 {DECLINE} 保留.
只输出 JSON：{{"choice": <slot 或 {DECLINE}>, "reason": "<简短中文依据>"}}"""
        reply = self.client.json_chat(system, humanize(json.dumps(payload, ensure_ascii=False)))
        if not isinstance(reply, dict) or type(reply.get("choice")) is not int:
            self.last_decline = "模型回复无效"
            return None
        choice = reply["choice"]
        if choice == DECLINE:
            self.last_decline = f"模型选择不使用：{str(reply.get('reason') or '')[:120]}"
            return None
        potion = next((p for p in potions if p.slot == choice), None)
        if potion is None:
            self.last_decline = f"模型给了不存在的槽位 {choice}"
            return None
        return Decision(
            action=UsePotion(potion.slot, None),
            reason=f"{potion.name}: {str(reply.get('reason') or '模型判断值得使用')[:120]}",
            source=Source.LLM,
            tool=self.name,
            extra={"llm": dict(self.client.last_meta or {})},
        )
