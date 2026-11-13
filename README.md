# sts2-autopilot

让《杀戮尖塔 2》自动游玩。分三层，边界清晰：

```
游戏内 Mod（C#）          决策层（Python）              评价 / 回放
sts2-bridge          →    contracts / router / agents   →   runs/*.jsonl
读状态、调动作队列          tools: LLM 路线 / 束搜索战斗
```

决策层的结构参考 [AttemorySystem/spire-agent](https://github.com/AttemorySystem/spire-agent)
（A20H 通关项目）：**战斗交给启发式算法，路线与构筑交给 LLM，工具藏在接口后面，主循环保持很小**。

已确定的目标方案：选路 LLM 结合当前版本攻略规划，构筑 LLM 按角色特点处理选牌、药水取舍、事件、购物与删牌等决策；两者可以调用模拟器，由实际战斗算法评估构筑与候选遭遇。战斗及模拟战斗全程零 LLM。当前仅部分能力已实现，详见 [架构与能力边界](docs/architecture.md) 和 [按序 TODO](docs/TODO.md)。

当前优先完成了选路与构筑的 LLM 接入：共同读取完整卡组、遗物、药水和资源，共享本局构筑计划；支持购物、删牌、选药、事件、遗物和篝火等构筑决策。铁甲战士已有基础构筑提示，版本攻略与模拟器调用后续补充。已通过离线验证，实际模型和真机效果尚未验证。

| 层 | 位置 | 职责 | 状态 |
| --- | --- | --- | --- |
| 桥接（游戏内 Mod） | `mod/sts2-bridge/` | 把游戏变成可被程序驱动的状态机 | ✅ 可编译，已加数值字段 |
| 决策层 | `brain/` | 路由 + 三块决策 + 回放 | ✅ 可运行 |
| 移植进 mod | 计划中 | 决策层搬进 C#，游戏内直接连 API | ⬜ |

## 目录

```
mod/sts2-bridge/     上游 STS2MCP 的 fork（MIT），含我们加的数值/卡组字段
mod/UPSTREAM.md      来源与本地改动记录
build.ps1            编译 + 可选安装
brain/
├── contracts.py     DecisionRequest / Decision（JSON 形状，可直译 C#）
├── ports.py         GamePort / ToolPort —— 两个接缝
├── router.py        界面 → 谁负责决策
├── agents/          game（循环）/ map / build / combat
├── cards/           每种卡牌的子类、类型与效果声明
├── combat/          战斗搜索 / 共享结算 / 敌人规则 / 药水顾问 / 真实战斗会话
├── sim/             完整战斗模拟、竞技场与独立四维评估器
├── tools/           map_router（LLM）/ build_flow / select_tool
├── transport.py     HTTP 桥接实现
├── replay.py        JSONL 日志，可离线重放
├── model.py         类型化状态（Card / Enemy / Battle / MapState …）
├── actions.py       类型化动作，自带合法性校验
└── cli.py           命令入口
docs/architecture.md  分层说明 + 移植到 mod 的路线
docs/TODO.md          模拟器、角色构筑和选路的实施顺序
```

## 构建与安装 mod

```powershell
.\build.ps1            # 编译
.\build.ps1 -Install   # 复制到 <游戏目录>\mods（需管理员，游戏需关闭）
```

Windows 默认执行策略会拦截未签名脚本：`powershell -ExecutionPolicy Bypass -File .\build.ps1`。

启动游戏后验证：

```powershell
curl.exe http://127.0.0.1:15526/
# {"message":"Hello from STS2 MCP v0.4.0","status":"ok"}
```

## 自己跑（不经过助手）

项目根目录有 `sts2.cmd` / `sts2.ps1`，它会自动找可用的 Python（PATH 里的，或 DSH 自带的），
不用你记路径、也不用额外装东西。

| 命令 | 作用 | 需要游戏在跑 |
| --- | --- | --- |
| `sts2 fight --encounter lagavulin --runs 30` | 离线模拟 30 场战斗，出胜率/回合/掉血 | ❌ |
| `sts2 fight-live` | **接管当前这场战斗**，打完就停 | ✅ |
| `sts2 run --offline` | 整局自动（战斗 + 启发式路线，全程不调 LLM） | ✅ |
| `sts2 dry` | 只决策一次，不发动作（调试用） | ✅ |

用法（cmd / PowerShell 都行）：

```powershell
cd C:\Users\69528\GithubProject\sts2-autopilot
.\sts2.cmd fight-live
```

```powershell
.\sts2.ps1 fight --encounter slimes --runs 20 --verbose
```

前提：游戏已启动、`sts2-bridge` 已加载（`curl.exe http://127.0.0.1:15526/` 返回 ok）。
`fight-live` 只在 **`state_type` 为 `monster`** 时工作；在别的界面会直接报"当前不在战斗中"。

## 决策层

纯标准库，不需要 `pip install`。

```bash
python -m brain.cli run --steps 400     # 自动游玩
python -m brain.cli dry                 # 只决策一次，不发动作
python -m brain.cli dry --from captures/combat-001.json   # 离线跑历史状态
python -m brain.cli combat --from captures/combat-001.json  # 战斗算法独立跑
python -m brain.cli combat --journal data/combat/run-*.jsonl  # 在战斗数据集上批量跑
python -m brain.cli replay logs/run-*.jsonl               # 汇总一局的决策分布
python -m unittest discover -s brain/tests -t .           # 124 个离线测试
```

### 战斗算法（可单独运行）

**单回合束搜索 + 手写估值**，不是 MCTS —— 我们没有 StS2 的快速模拟器，
现有离线引擎规则覆盖尚不完整，当前没有采用完整战斗深搜。

```
损伤 = 牌面伤害 + 力量，易伤 ×1.5，虚弱 ×0.75
格挡 = 牌面格挡 + 敏捷，脆弱 ×0.75
得分 = -敌人总血 - 血量权重×未格挡伤害 + 0.1×溢出格挡 + 状态价值
```

`血量权重`是**自适应**的：满血时 1 点血 ≈ 0.4 敌人血量（可以换），
残血时 ≈ 2.0（优先保命）。常数权重会让 Bot 一直硬换血直到死。

`decide_state(raw_json) -> Decision` 是纯函数，不碰网络和文件 ——
这是以后 C# 移植要逐位对齐的入口，也是离线基准测试的入口。

### 独立跑一整场战斗（不开游戏）

```powershell
python -m brain.cli fight --deck silent --encounter nibbit --runs 20 --verbose
```

```
  seed=000 胜  4回合 剩余62/70 掉血 8 出牌12
  seed=001 胜  3回合 剩余54/70 掉血16 出牌9
  ...
{"runs": 20, "win_rate": 1.0, "turns_avg": 3.4,
 "damage_taken_avg": 13, "hp_left_avg": 57, ...}
```

`brain/sim/` 是一个本地战斗引擎：抽牌、洗牌、能量、格挡、敌人意图循环、胜负判定都在里面。
**它复用的就是真机那套决策函数**——每回合把模拟状态渲染成 `/state` 的格式，喂给
`decide_state`，再把返回的动作应用回模拟。相同状态下复用相同决策入口；但规则和状态覆盖不完整，离线整场结果不能直接等同于真机表现。

现有引擎不包含遗物和药水，卡牌与怪物机制仅部分覆盖。内置遭遇是简化基准，数值不代表真实游戏，不能直接用于选路的精英风险预测。可信模拟器及 LLM 调用入口仍在 [实施计划](docs/TODO.md) 中。

现存场景（`--encounter`）：`nibbit`（44血/12伤）、`slimes`（三只）、`jaw_worm`（攻击+强化循环）、
`lagavulin`（109血/18伤，用来测上限）。

调参闭环：

```powershell
# 改 brain/combat/search.py 里的 HP_WEIGHT_* / OVERBLOCK_WEIGHT
python -m brain.cli fight --encounter lagavulin --runs 30   # 几秒钟出结果
```

### LLM 配置

不配也能跑 —— 没有 key、超时、返回非法选项，都会退回启发式，不会卡住整局。

配置后，选路与构筑共用现有 LLM 客户端；启动时不加 `--offline` 即启用。战斗出牌、喝药与战斗内选牌仍使用算法。购物每次只执行一笔，删牌分购买服务和选牌确认，满槽药水替换分丢弃和重新选择领取 / 购买；每步重新读取状态。模型只能返回程序提供的候选编号，不能直接生成任意游戏动作。

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."     # 默认 https://api.deepseek.com/v1
$env:LLM_MODEL        = "deepseek-chat"
```

也可以放进 `brain/.env`（已 gitignore）：

```
DEEPSEEK_API_KEY=sk-...
```

### 测试 LLM 链路（不需要真 key）

```bash
python tools/mock_llm.py 18777 0
$env:OPENAI_BASE_URL = "http://127.0.0.1:18777/v1"; $env:LLM_API_KEY = "mock"
python -m brain.cli dry
```

## 为什么战斗不用 LLM

战斗是完全信息、状态空间小的问题，本质是搜索；LLM 慢、贵，而且算不准
易伤/力量的结算顺序。参考项目用 MCTS + 一个 C++ 模拟器，我们没有对应模拟器，
所以战斗工具目前是**一回合束搜索**——接口一致，以后换实现只动 `combat/search.py`。

## 存档：mod 档和原版档是分开的

```
%APPDATA%\SlayTheSpire2\steam\<steamid>\profile1          ← 原版档
%APPDATA%\SlayTheSpire2\steam\<steamid>\modded\profile1   ← 装 mod 后的档
```

第一次带 mod 启动时角色全锁是正常现象。直接复制会被 Steam 云按云端版本还原，
必须先关闭该游戏的「Steam 云」再复制，或用 `--force-steam=off` 启动。

## 兼容性

| 项 | 值 |
| --- | --- |
| 游戏版本 | `v0.107.1` |
| Mod | `sts2-bridge` fork（上游 STS2MCP v0.4.0） |
| 运行时 | .NET 9（游戏自带） |
| 构建 | .NET 9 SDK |

编译通过只证明 API 没改名，不证明运行时行为正确 —— 每个决策场景都要对着跑起来的游戏验证。

## 许可

`mod/` 下代码为 MIT，版权归上游作者（见 `mod/sts2-bridge/LICENSE`）。
其余部分同样以 MIT 发布。
