"""战斗中的两个单卡顾问: 节奏牌 (祭品/放血) 与无法估值的能力牌.

两者共用 ``CardAdvisor`` 的问答与缓存逻辑, 只是触发条件与提问措辞不同。
"""

from __future__ import annotations

from typing import Optional

from ..llm import LLMClient
from .card_advisor import CardAdvisor
from .search import _TEMPO_HP_CARDS, unmodeled_power


class TempoAdvisor(CardAdvisor):
    """祭品 / 放血: 用生命换能量与抽牌, 价值一半在抽到的牌上."""

    name = "tempo_advisor"

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        super().__init__(
            client,
            matches=lambda card: (card.id in _TEMPO_HP_CARDS
                                  or card.name in _TEMPO_HP_CARDS),
            task='现在要不要打出"用生命换能量与抽牌"的牌。'
                 '这类牌的价值取决于抽到什么, 战斗搜索只能用牌库平均值估算。')


class PowerAdvisor(CardAdvisor):
    """效果没有被本地建模的能力牌: 在模型里等于白板, 只能由模型判断."""

    name = "power_advisor"

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        super().__init__(
            client,
            matches=unmodeled_power,
            task="现在要不要打出这张能力牌。"
                 "它的效果没有被本地战斗模型建模, 模拟里等于一张白板, "
                 "请按 description 的实际效果判断现在打出是否划算。")
