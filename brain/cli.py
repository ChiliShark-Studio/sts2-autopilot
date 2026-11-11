"""命令行入口：把 `python -m brain.cli xxx` 的参数分发到对应的处理函数。

    python -m brain.cli run [--steps N] [--offline]   整局自动
    python -m brain.cli dry                          只决策一次，不发动作
    python -m brain.cli replay FILE                  汇总一局录像
    python -m brain.cli combat --from FILE           战斗算法独立跑
    python -m brain.cli fight --runs N               离线打完整场战斗
    python -m brain.cli fight-live                   接管游戏里当前这场战斗

这里只做"读参数、选函数、打印结果"，不含任何战斗或路线逻辑。
真正的算法在 brain/tools/combat_search.py，战斗规则在 brain/sim/engine.py。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import replay
from .agents.game import GameAgent, StopRun, UnsupportedScreen
from .factory import DEFAULT_BASE, build_agent
from .transport import Bridge, BridgeError


def _print_decision(step: int, decision) -> None:
    """每走一步打印一行：序号、动作、来源、理由。"""
    action = decision.action.describe() if decision.action else "（等待）"
    print(f"[{step:03d}] {action} | {decision.source.value}/{decision.tool} | {decision.reason}")


def cmd_run(args: argparse.Namespace) -> int:
    """整局自动跑：地图、战斗、奖励、选牌都由 agent 自己决定。"""
    agent = build_agent(args.base, offline=args.offline, journal_dir=Path("logs"),
                        stop_after_run=args.one_run)
    try:
        outcome = agent.run(max_steps=args.steps, settle=args.settle, on_step=_print_decision)
    except (UnsupportedScreen, BridgeError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 7
    # outcome 可能是 game_over / unsupported / max_steps / stuck 等，都算正常结束
    print(f"outcome: {outcome}")
    return 0


def cmd_dry(args: argparse.Namespace) -> int:
    """只算一次决策并打印，不发给游戏。用于调试策略。"""
    agent = build_agent(args.base, offline=args.offline)

    # --from 给文件时读历史状态；否则读游戏当前状态
    if args.from_file:
        from .model import GameState

        state = GameState.parse(json.loads(Path(args.from_file).read_text(encoding="utf-8-sig")))
    else:
        state = Bridge(args.base).state()

    try:
        decision = agent.decide(state)
    except (StopRun, UnsupportedScreen) as exc:
        # 游戏结束或遇到没有策略的界面
        print(f"no decision: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(decision.to_wire(), ensure_ascii=False, indent=2))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """汇总一份录像：每步落在哪个界面、决策来自哪个工具。"""
    path = Path(args.file)
    if not path.exists():
        print(f"no such run: {path}", file=sys.stderr)
        return 2

    if args.llm:
        for entry in replay.llm_decisions(path):
            decision = entry.get("decision") or {}
            llm = (decision.get("extra") or {}).get("llm") or {}
            print(f"[{entry.get('step')}] {entry.get('screen')} / {decision.get('tool')} -> {decision.get('action')}")
            print(f"  reason : {decision.get('reason')}")
            print(f"  model  : {llm.get('model')} | usage={llm.get('usage')} | {llm.get('latency_ms')}ms")
            prompt = str(llm.get("prompt") or "")
            if prompt:
                print(f"  prompt : {prompt[:400]}")
            print()
        return 0

    print(json.dumps(replay.summarize(path), ensure_ascii=False, indent=2))
    return 0


def _combat_benchmark(states: list[dict], *, verbose: bool, limit: int) -> int:
    """战斗算法的批量基准：输入一串状态，输出决策分布和耗时。

    不连游戏、不调 LLM，纯本地计算。
    """
    import time

    from .tools.combat_search import decide_state

    rows = states[:limit]
    if not rows:
        print("no combat states found", file=sys.stderr)
        return 3

    actions: dict[str, int] = {}
    timings: list[float] = []
    waits = 0

    for raw in rows:
        started = time.perf_counter()
        decision = decide_state(raw)
        timings.append((time.perf_counter() - started) * 1000)  # 毫秒

        if decision.action is None:
            waits += 1  # 还没轮到我方，或动画未结算
            continue

        name = decision.action.kind
        actions[name] = actions.get(name, 0) + 1
        if verbose:
            print(f"{decision.action.describe():24} | {decision.reason}")

    # 排序后取中位数和 p95，p95 反映"最慢的那几次"有多慢
    timings.sort()
    mid = timings[len(timings) // 2]
    p95 = timings[int(len(timings) * 0.95) - 1] if len(timings) > 1 else mid

    print(json.dumps({
        "states": len(rows),
        "decisions": len(rows) - waits,
        "waits": waits,
        "actions": actions,
        "ms_median": round(mid, 1),
        "ms_p95": round(p95, 1),
        "ms_max": round(timings[-1], 1),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_combat(args: argparse.Namespace) -> int:
    """战斗算法独立跑：单个状态，或一整份录像里的所有战斗状态。"""
    from .replay import Journal

    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text(encoding="utf-8-sig"))
        return _combat_benchmark([raw], verbose=True, limit=1)

    if args.journal:
        entries = list(Journal(Path(args.journal)))
        # 只取 state_type 为 monster 的条目（真正的战斗状态）
        states = [e["state"] for e in entries
                  if (e.get("state") or {}).get("state_type") == "monster"]
        return _combat_benchmark(states, verbose=args.verbose, limit=args.limit)

    print("give --from FILE or --journal FILE", file=sys.stderr)
    return 2


def cmd_fight(args: argparse.Namespace) -> int:
    """离线打完整场战斗：本地模拟器 + 同一个战斗算法，不连游戏。"""
    from .sim import arena, scenarios

    deck = scenarios.starter(args.deck)
    batch = arena.run_batch(
        deck, lambda: scenarios.encounter(args.encounter),
        runs=args.runs, hp=args.hp, max_hp=args.hp,
    )

    # --verbose 时逐场打印，否则只给汇总
    if args.verbose:
        for result in batch.results:
            flag = "胜" if result.won else "负"
            print(f"  seed={result.seed:03d} {flag} {result.turns:2d}回合 "
                  f"剩余{result.hp_left:2d}/{result.max_hp} 掉血{result.damage_taken:2d} "
                  f"出牌{result.cards_played}")

    print(json.dumps(batch.summary(), ensure_ascii=False, indent=2))
    return 0


def cmd_fight_live(args: argparse.Namespace) -> int:
    """接管游戏里当前这一场战斗，打完就停；不碰地图和奖励。"""
    from .combat_session import NotInCombat, fight_current

    # offline=True：根本不创建 LLM 客户端，确保战斗过程零 LLM
    agent = build_agent(args.base, offline=True, journal_dir=Path("logs"))
    try:
        summary = fight_current(agent.port, agent, max_steps=args.steps,
                                settle=args.settle, verbose=not args.quiet)
    except NotInCombat as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """注册所有子命令。每个子命令的 func 就是上面那些 cmd_* 函数。"""
    parser = argparse.ArgumentParser(prog="brain.cli")
    parser.add_argument("--base", default=DEFAULT_BASE, help="桥接地址，默认 127.0.0.1:15526")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 整局自动
    run = sub.add_parser("run", help="整局自动跑，直到结束或遇到不支持的界面")
    run.add_argument("--steps", type=int, default=400, help="最多走多少步")
    run.add_argument("--settle", type=float, default=0.6, help="每步之间等多久（秒）")
    run.add_argument("--offline", action="store_true", help="不创建 LLM 客户端")
    run.add_argument("--one-run", action="store_true",
                     help="跑完一局就退出（不自动重开），方便逐局分析")
    run.set_defaults(func=cmd_run)

    # 单次决策
    dry = sub.add_parser("dry", help="只决策一次，不发动作")
    dry.add_argument("--offline", action="store_true", help="不创建 LLM 客户端")
    dry.add_argument("--from", dest="from_file", default=None,
                     help="从文件读状态，而不是读游戏当前状态")
    dry.set_defaults(func=cmd_dry)

    # 录像汇总
    rep = sub.add_parser("replay", help="汇总一份录像")
    rep.add_argument("file", help="logs/run-*.jsonl")
    rep.add_argument("--llm", action="store_true", help="只看模型决策（含 prompt/reply）")
    rep.set_defaults(func=cmd_replay)

    # 战斗算法基准
    combat = sub.add_parser("combat", help="战斗算法独立跑（不连游戏）")
    combat.add_argument("--from", dest="from_file", default=None, help="单个状态 JSON 文件")
    combat.add_argument("--journal", default=None, help="从录像里提取所有战斗状态")
    combat.add_argument("--limit", type=int, default=200, help="最多跑多少个状态")
    combat.add_argument("--verbose", action="store_true", help="逐个打印决策")
    combat.set_defaults(func=cmd_combat)

    # 离线整场战斗
    fight = sub.add_parser("fight", help="离线打完整场战斗（不连游戏、不调 LLM）")
    fight.add_argument("--deck", default="silent", help="卡组：silent | ironclad")
    fight.add_argument("--encounter", default="nibbit",
                       help="遭遇：nibbit | slimes | jaw_worm | lagavulin")
    fight.add_argument("--runs", type=int, default=20, help="跑多少场")
    fight.add_argument("--hp", type=int, default=70, help="初始血量")
    fight.add_argument("--verbose", action="store_true", help="逐场打印结果")
    fight.set_defaults(func=cmd_fight)

    # 接管游戏内当前战斗
    live = sub.add_parser("fight-live", help="接管游戏里当前这场战斗")
    live.add_argument("--steps", type=int, default=300, help="最多走多少步")
    live.add_argument("--settle", type=float, default=0.5, help="每步之间等多久（秒）")
    live.add_argument("--quiet", action="store_true", help="不逐条打印决策")
    live.set_defaults(func=cmd_fight_live)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
