"""独立 JSON 命令入口，不连接游戏或模型。"""

import argparse
import json
import sys
from pathlib import Path

from .core import compare, evaluate, fight_boss


def main() -> int:
    parser = argparse.ArgumentParser(description="输入牌组，分别评估伤害、防御、费用、启动")
    parser.add_argument("--from", dest="input_file", required=True, help="牌组 JSON 文件")
    parser.add_argument("--candidate", help="可选：待加入的单张卡牌 JSON 文件")
    parser.add_argument("--boss", choices=("vantom", "ceremonial_beast"), help="将同一构筑交给战斗模拟器打 Boss")
    parser.add_argument("--max-turns", type=int, default=50, help="Boss 模拟回合上限，默认 50")
    args = parser.parse_args()
    if args.boss and args.candidate:
        parser.error("--boss 与 --candidate 不能同时使用")
    try:
        raw = json.loads(Path(args.input_file).read_text(encoding="utf-8-sig"))
        candidate = (json.loads(Path(args.candidate).read_text(encoding="utf-8-sig"))
                     if args.candidate else None)
        result = (fight_boss(raw, args.boss, args.max_turns) if args.boss else
                  compare(raw, candidate) if candidate is not None else evaluate(raw))
    except (OSError, ValueError, TypeError) as exc:
        print(f"评估失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    statuses = [result["before"]["status"], result["after"]["status"]] if candidate is not None else [result["status"]]
    return 0 if all(s == "ok" for s in statuses) else 3


if __name__ == "__main__":
    raise SystemExit(main())
