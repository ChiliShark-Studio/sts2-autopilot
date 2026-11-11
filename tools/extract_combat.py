"""Extract combat decisions from run journals into a small, commit-able dataset.

Why: `logs/` holds every step of every run (1-3 MB each, mostly waiting), so it
stays git-ignored.  What we actually tune the combat algorithm on is far smaller:
the states where a combat decision was made, plus the decision itself.

    python tools/extract_combat.py logs data/combat

Output is one JSONL per source run:

    {"step": 42, "fight": "1-13", "act": 1, "floor": 13, "turn": 2,
     "enemies": [{"name": "飞蝇菌子", "hp": 49}], "hand": ["打击", "防御"],
     "energy": 3, "hp": 34, "max_hp": 70, "state": {...}, "decision": {...}}

The top-level summary duplicates what is inside ``state`` on purpose: it makes
the file readable (and greppable) without parsing a 7 KB blob, and ``fight``
groups the decisions that belong to the same encounter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

COMBAT_SCREENS = {"monster", "hand_select", "card_select"}


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    """Pull the context a human (or a filter) wants without opening ``state``."""
    battle = state.get("battle") or {}
    player = state.get("player") or {}
    run = state.get("run") or {}
    act = run.get("act")
    floor = run.get("floor")
    return {
        "fight": f"{act}-{floor}",
        "act": act,
        "floor": floor,
        "turn": battle.get("round"),
        "enemies": [
            {"name": e.get("name"), "hp": e.get("hp")}
            for e in (battle.get("enemies") or []) if isinstance(e, dict)
        ],
        "hand": [c.get("name") for c in (player.get("hand") or []) if isinstance(c, dict)],
        "energy": player.get("energy"),
        "hp": player.get("hp"),
        "max_hp": player.get("max_hp"),
    }


def iter_journal(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a truncated tail should not kill the whole file


def extract(path: Path) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for entry in iter_journal(path):
        screen = entry.get("screen")
        if screen not in COMBAT_SCREENS:
            continue
        decision = entry.get("decision") or {}
        # "none"/"wait" rows are just "enemy turn, nothing to do" -- no signal.
        if decision.get("tool") in (None, "", "none"):
            continue
        state = entry.get("state")
        if not isinstance(state, dict):
            continue
        record = {"step": entry.get("step"), "screen": screen}
        record.update(summarize(state))
        record["state"] = state
        record["decision"] = decision
        kept.append(record)
    return kept


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: python tools/extract_combat.py <logs-dir-or-file>... <out-dir>", file=sys.stderr)
        return 2

    *sources, out_arg = args
    out_dir = Path(out_arg)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for raw in sources:
        src = Path(raw)
        files.extend(sorted(src.glob("*.jsonl")) if src.is_dir() else [src])

    total = 0
    for path in files:
        rows = extract(path)
        if not rows:
            continue
        target = out_dir / f"{path.stem}.jsonl"
        with target.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        total += len(rows)
        print(f"{path.name}: {len(rows)} 条战斗决策 -> {target}")

    print(f"合计 {total} 条，输出目录 {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
