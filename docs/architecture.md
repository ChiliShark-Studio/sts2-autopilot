# 架构

## 参考对象

[AttemorySystem/spire-agent](https://github.com/AttemorySystem/spire-agent)
（《杀戮尖塔》A20H 通关项目）。它把游戏拆成三个决策单元，各自拥有一个 agent：

```
GameAgent（循环 + 路由）
  ├─ MapAgent    → MapTool     LLM 排序路线 + 确定性门槛
  ├─ BuildAgent  → CardPicker  模板搜索，未决交 LLM
  └─ CombatAgent → CombatTool  MCTS over sts_lightspeed
```

三条原则值得照搬，也是我们分层的依据：

1. **主循环必须小。** 它的 `game_agent.py` 只有 2.4KB；复杂度在 tool 里。
2. **`DecisionRequest` / `Decision` 是唯一契约。** 换实现不改主循环。
3. **确定性优先。** 数值精确、可验证的部分交给算法；只有"需要权衡"的判断才给 LLM。

我们和它的差别：它跑一代 + Java mod + `gym-sts`，我们跑二代 + C# mod + HTTP 桥。
所以我们的 `transport.py` 是 HTTP，它的 adapter 是子进程。

## 分层

```
                 ┌──────────────────────────────┐
                 │ GameAgent                    │  agents/game.py
                 │ state → route → decide → act │  只做生命周期 + 转发
                 └───────────┬──────────────────┘
                             │ router.route(state)
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         MapAgent       BuildAgent      CombatAgent      agents/*.py
              │              │              │
              ▼              ▼              ▼
        MapRouterTool   BuildFlowTool  CombatSearchTool   tools/*.py
          (LLM+兜底)      (奖励/选牌)     (束搜索)
```

| 文件 | 职责 | 不该做的事 |
|---|---|---|
| `contracts.py` | 请求/决策的数据形状 | 不碰 I/O、不碰时间 |
| `ports.py` | `GamePort` / `ToolPort` 协议 | 不含实现 |
| `router.py` | `state_type` → 决策单元 | 不含策略 |
| `agents/*.py` | 把请求转给工具 | 不含算法 |
| `tools/*.py` | 真正的算法/LLM 调用 | 不直接发动作 |
| `transport.py` | HTTP 桥接 | 不含决策 |
| `replay.py` | 落盘/读回 | 不参与决策 |

## 为什么这样切

**为了能搬进 mod。** 这是最终目标：游戏内直接连 API 自动战斗。所以：

- `contracts.py` 里的结构全部是 JSON 安全的，C# 可以直接镜像；
- 决策层不碰文件/时钟/网络（只有 `tools/map_router` 调 HTTP，这是它的职责）；
- 工具是接口的实例，替换实现不需要改 agent。

**为了能离线回放。** `replay.py` 存的是**原始 wire 状态**，不是类型化对象，
所以 Python 和以后的 C# 读同一份日志。可以拿历史状态重跑新策略，做 A/B。

## 移植到 mod 的路线

目标形态：mod 内自带决策循环，`HttpClient` 直连 DeepSeek，不再需要外部 Python。

| Python | C# | 难度 |
|---|---|---|
| `contracts.py` | `DecisionContracts.cs`（`record` + `System.Text.Json`） | 低 |
| `model.py` | `GameSnapshot.cs`（现有 `/state` 的 DTO） | 低 |
| `router.py` | `DecisionRouter.cs`（`switch (state_type)`） | 低 |
| `tools/combat_search.py` | `CombatSearch.cs`（纯计算，无依赖） | 中 |
| `tools/map_router.py` | `MapRouter.cs`（`HttpClient` → OpenAI 兼容端点） | 中 |
| `tools/build_flow.py` | `BuildFlow.cs` | 低 |
| `agents/game.py` | `AutoPlayLoop.cs`（`Node._Process` + `CallDeferred`） | 中 |
| `transport.py` | **不需要** —— 直接调内部 API | — |
| `replay.py` | `JsonlJournal.cs`（`File.AppendAllText`） | 低 |

建议顺序：

1. **先搬契约和模型**，让 C# 能解析自己产出的 `/state`；
2. **再搬 combat_search**（纯函数，最好测，收益最大）；
3. **接 LLM**（mod 里已有 HttpClient，参考上游 STS2MCP 的用法）；
4. **最后接主循环**，用 Godot 的 `_Process` 驱动，注意所有游戏调用要回主线程。

关键约束：

- **不要另造协议。** C# 侧直接复用 `/api/v1/singleplayer` 的字段名，
  这样 Python 和 C# 能跑同一份回放。
- **保留 Python 层**。移植后用回放日志做"金样本测试"：同一状态，C# 和 Python
  必须给出同样的动作；不一致就是移植 bug。
- **双模式共存**。mod 自驱 和 外部 brain 可以并存，用配置切换——
  调试时代码在 Python 里改更快。

## 已知缺口

| 缺口 | 现状 | 影响 |
|---|---|---|
| 商店 / 事件 / 篝火 | 未实现，遇到就停 | 爬不到第二层 |
| 药水 | 战斗工具不喝药 | 白给的价值浪费 |
| 血量权重 | 常数 0.6 | 残血时仍然硬换血 |
| 战斗搜索 | 束搜索，非 MCTS | 上限低于参考项目 |
| 地图分叉 | 没验证过真实分支 | LLM 路线能力未验证 |
