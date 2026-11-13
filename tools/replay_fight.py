"""用本地模拟器重放录像里的战斗决策, 对比模拟出来的敌人血量与游戏实际血量.

    python tools/replay_fight.py data/combat/underdocks-colony-20261114.jsonl

这是回测入口: 录像来自 ``tools/extract_combat.py``, 算法或模拟器改动后跑一遍即可.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.combat.backtest import compare  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python tools/replay_fight.py data/combat/<录像>.jsonl", file=sys.stderr)
        return 2
    report = compare(Path(args[0]))
    print(f"录像: {report.path}")
    for item in report.mismatches:
        print(f"  step {item.step:>4}  打出 {item.card:<6} "
              f"模拟 {item.simulated}  游戏 {item.recorded}")
    print(f"\n一致 {report.matched} 处, 不一致 {len(report.mismatches)} 处, 跳过 {report.skipped} 处")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
