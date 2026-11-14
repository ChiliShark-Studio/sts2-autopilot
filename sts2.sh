#!/usr/bin/env bash
# Launcher for sts2-autopilot on macOS / Linux (counterpart of sts2.cmd).
#
#   ./sts2.sh fight --encounter lagavulin --runs 30
#   ./sts2.sh run --one-run
#   ./sts2.sh dry            # 只看一次决策, 不发给游戏
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
cd "$root"

# 优先用项目里的 .venv, 再退回系统 python3.
if [ -x "$root/.venv/bin/python" ]; then
  py="$root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  py="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  py="$(command -v python)"
else
  echo "ERROR: python3 not found (install Python 3.11+)." >&2
  exit 1
fi

exec "$py" -m brain.cli "$@"
