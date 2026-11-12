# AGENTS.md

给接手的 agent 看：目标、约定、命令、当前状态。项目说明见 `README.md`，分层设计见 `docs/architecture.md`，按序实施清单见 `docs/TODO.md`。

## 目标

用自动游玩把《杀戮尖塔 2》**铁甲战士打到 A10 通关**。

当前档案从 **A0** 起步（不搬原版档，也不用旧存档）。游戏规则是赢 A(n) 才解锁 A(n+1)，
所以要打 A10 需要先依次拿下 A0…A9。

## 现状（2026-11-12）

| 项 | 值 |
|---|---|
| 最好成绩 | A0 第 15 层阵亡（6/70 血，败给小啃兽） |
| 档案 | mod 档，铁甲战士 A0，0 胜 |
| 已覆盖界面 | 主菜单 / 地图 / 战斗 / 奖励 / 选牌（战斗内+网格）/ 篝火 / 宝箱 / 商店 / 事件 / 结算 |
| 战斗算法 | 单回合束搜索 + 自适应血量权重，**零 LLM** |
| 路线决策 | DeepSeek（无 key 自动退启发式） |
| 离线测试 | 50 个用例，全过 |

## 里程碑

| 阶段 | 目标 | 关键工作 |
|---|---|---|
| M1 | 打赢一局 A0 | 可信模拟案例、铁甲战士构筑 LLM、模拟辅助选路 |
| M2 | A0 稳定胜率 ≥50% | 战斗调参、路线策略 |
| M3 | A1–A2 | 自动重开循环、逐局复盘 |
| M4 | A3–A9 | 同上，难度递增 |
| M5 | A10 通关 | 针对性优化 |

## 目录

```
mod/sts2-bridge/     游戏内桥接（上游 MIT fork + 我们的数值/卡组字段补丁）
brain/               决策层
  contracts.py       DecisionRequest / Decision（JSON 形状，可直译 C#）
  ports.py           GamePort / ToolPort
  router.py          界面 → 谁负责决策
  agents/            game 循环 + map / build / combat / select
  tools/             map_router(LLM) / combat_search(束搜索) / build_flow / select_tool
  sim/               离线战斗引擎 + 竞技场
  replay.py          JSONL 日志
  cli.py             命令入口（中文注释）
tools/               ApiProbe（反射探针）、mock_llm、extract_combat
logs/                原始日志：每步完整状态，**不入库**（1-3MB/局）
data/combat/         从日志里抽出的战斗决策数据集，**入库**，用于调优
```

## 命令

```powershell
.\sts2.cmd fight --encounter lagavulin --runs 30   # 离线打整场，不连游戏
.\sts2.cmd fight-live                              # 接管游戏里当前这场战斗
.\sts2.cmd run --offline                           # 整局自动（不调 LLM）
.\sts2.cmd run --offline --one-run                 # 跑完一局就退出，便于逐局分析
.\sts2.cmd dry --from captures/combat-001.json     # 单状态决策
.\sts2.cmd replay logs\run-*.jsonl                 # 决策分布
.\sts2.cmd replay logs\run-*.jsonl --llm           # 只看模型决策（含 prompt/reply/token）
python -m unittest discover -s brain/tests -t .     # 96 个离线测试
```

构建 mod：`powershell -ExecutionPolicy Bypass -File .\build.ps1 [-Install]`

## 约定

1. **战斗过程不调 LLM。** `factory.py` 当前把 client 传给路线与构筑工具，战斗工具不接收。模拟战斗也复用实际启发式策略，全程零 LLM。
2. **每一步重读状态。** 不缓存、不假设上一步的结论仍然成立。
3. **未知界面停下，不猜。** 返回 `no_decision` 比乱点安全。
4. **状态字段以 mod 的 `/state` 为准。** 不自己发明字段名。
5. **代码标识符英文，注释中文。** `cli.py` 已按此标准。
6. **`brain/.env` 绝不提交**（已在 .gitignore）。

## 已知坑

| 坑 | 处理 |
|---|---|
| 游戏必须从 Steam 启动 | `steam.exe -applaunch 2868840` |
| 沙箱里 git/curl 的 TLS 失败 | `git -c http.sslBackend=openssl ...` |
| PowerShell 读无 BOM 的 .ps1 乱码 | 启动脚本保持纯 ASCII |
| 卡牌奖励 `skip` 不消耗条目 | 跳过之后要 `proceed`，不能再领 |
| 网格选牌是 toggle | 只点未选中的牌，否则反复选中/取消 |
| 战斗内手牌选择 | 用 `combat_select_card`，不是 `select_card` |
| 主菜单可能被 epoch 揭示挡住 | 先 `menu_select timeline` 处理 |
| 地图切换瞬间 `next_options` 为空 | 视为等待，不是卡死 |

`state_type` 取值：`monster` / `map` / `rewards` / `card_reward` / `hand_select` / `card_select` / `rest_site` / `shop` / `treasure` / `event` / `menu` / `game_over`。

## 数据

分两层，别混：

| 目录 | 内容 | 入库 |
|---|---|---|
| `logs/*.jsonl` | 每一步的完整状态（含大量等待帧），一局 1–3 MB | ❌ ignore |
| `data/combat/*.jsonl` | 只保留**真正做了战斗决策**的状态 + 决策 | ✅ |

从日志抽取：

```powershell
python tools\extract_combat.py logs data\combat
```

每条记录的形状（顶层摘要是给人看和过滤用的，完整状态在 `state` 里）：

```json
{"step": 90, "fight": "1-13", "ascension": 0, "act": 1, "floor": 13, "turn": 3,
 "enemies": [{"name": "飞蝇菌子", "hp": 49}],
 "hand": ["连续反弹", "突然一拳", "防御"],
 "energy": 3, "hp": 34, "max_hp": 70,
 "state": {...}, "decision": {"action": {...}, "reason": "..."}}
```

用途：

- 离线重放：`brain.cli combat --journal data/combat/xxx.jsonl`
- 调参对比：同一批状态跑不同参数，看决策变化
- 回归验证：改算法后确认没有变差

新增对局后跑一次抽取脚本，把新的 `data/combat/*.jsonl` 一起提交。

## 下一步

已确定三部分分工：战斗由启发式算法处理；选路由 LLM 结合当前版本攻略和模拟结果规划；构筑由按角色定制的 LLM 负责选牌、药水取舍、事件、商店购买与删牌、遗物和篝火决策。战斗内临时选牌和喝药时机仍由战斗算法负责。

LLM 输入构筑和待评估遭遇，调用模拟器获得结果，不自行心算胜率。复用 `brain/sim/`；现有引擎不支持遗物、药水和完整游戏规则，内置简单遭遇不代表真实游戏，暂不能用于可信的精英风险预测。

当前奖励选牌已可使用 LLM，商店 / 事件 / 篝火主要是启发式；商店没有遗物购买与主动删牌策略。战斗已有部分药水决策，但搜索没有模拟抽牌后续和遗物触发。

按 `docs/TODO.md` 逐项推进，首先完成 **T01：模拟输入 / 输出契约与一个真实精英案例的覆盖范围**。TODO 是计划，不代表实现授权；每项实施、测试代码和 Git 提交仍遵循协作确认规则。自动重开与 A0 至 A10 的逐局复盘保留为后续目标。
