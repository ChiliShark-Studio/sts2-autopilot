"""独立牌组四维评估工具。

Python: evaluate({"deck": ["打击", "防御"]})
命令行: python -m brain.sim.evaluator --from deck.json

只输出伤害、防御、费用、启动及规则覆盖情况，不生成综合强度分。
"""

from .core import compare, evaluate, fight_boss

__all__ = ["evaluate", "compare", "fight_boss"]
