# 上游来源

本目录是 vendor 进来的第三方源码，**并已由我们本地修改**（见下方"本地改动"）。
用 vendor 而不是 submodule，是因为要改它，改动应该能直接提交、直接 diff。

| 项 | 值 |
|---|---|
| 上游 | <https://github.com/Gennadiyev/STS2MCP>（MIT，© Yikun Ji） |
| 使用的 fork | <https://github.com/enderzcx/sts2-bridge>（MIT） |
| 提交 | `f042ebb66495ee3995d72b8059bedfdb8a582bcb` |
| 引入日期 | 2026-11-11 |
| 验证的游戏版本 | `v0.107.1` |

## 本地改动

### 1. 结构化数值字段（`McpMod.StateBuilder.cs`）

上游只把卡牌和意图渲染成中文文本，决策层要拿数字只能解析字符串。改动用游戏自己的
`DynamicVars` 直接取值，避免文本解析：

| 位置 | 新增字段 | 来源 |
|---|---|---|
| `hand[]` | `damage` / `damage_base` | `CardModel.DynamicVars["Damage"]` 的 `IntValue` / `BaseValue` |
| `hand[]` | `block` / `block_base` | 同上，`"Block"` |
| `hand[]` | `hits` | `"Repeat"`，最小 1 |
| `hand[]` | `vulnerable` / `weak` | `"Vulnerable"` / `"Weak"` |
| `enemies[].intents[]` | `damage` / `hits` | `AttackIntent.DamageCalc()`（`Func<decimal>`）与 `Repeats`；`DeathBlowIntent` 同理 |

`damage` 与 `damage_base` 都输出，是因为 `PreviewValue` 可能已含预览修正、`BaseValue` 是牌面值；
实测在基础牌上两者相同，带力量时需再核对。

## 这个 fork 比上游多了什么（原始补丁）

只有一个补丁：动作就绪字段。

| 字段 | 含义 |
|---|---|
| `battle.action_running` | 动作执行器正在跑 |
| `battle.action_queue_empty` | 动作队列已空 |
| `battle.ready_for_action` | 以上满足，且未禁用玩家操作、手牌 UI 可出牌 |

上游的 `is_play_phase` 在动画播放期间仍然是 `true`，控制器据此出牌会对着半结算状态操作。
这三个字段是"什么时候能安全发下一条命令"的唯一可靠信号。

## 重新同步上游

```powershell
git clone https://github.com/enderzcx/sts2-bridge.git .tmp/sts2-bridge
# 对比差异，人工挑选，不要直接覆盖 —— 我们的改动也在里面
```

## 许可

MIT。原始版权声明见 `mod/sts2-bridge/LICENSE`，分发时保留。
