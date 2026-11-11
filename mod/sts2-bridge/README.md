# STS2 Bridge

《杀戮尖塔 2》的本地 Mod，把游戏变成一台可以被程序驱动的状态机：读结构化状态、调用游戏原有的动作队列。**不截图、不模拟点击、不修改游戏逻辑。**

这是 [Gennadiyev/STS2MCP](https://github.com/Gennadiyev/STS2MCP)（MIT，500★）的 fork，只加了一个补丁：**动作就绪字段**。配套的控制器是 [spire-ctl](https://github.com/enderzcx/spire-ctl)。

## 为什么需要这个补丁

控制器看不到画面。它必须在"上一张牌已经结算完"之后才敢出下一张，否则会对着一个正在播放动画的状态操作。上游的状态里有 `is_play_phase`，但那只说明"轮到玩家"，不代表"现在能出牌"——动画期间它仍然是 true。

补丁补上四个字段：

| 字段 | 含义 |
|---|---|
| `battle.action_running` | 动作执行器正在跑 |
| `battle.action_queue_empty` | 动作队列已空 |
| `battle.ready_for_action` | 以上都满足，且未禁用玩家操作、手牌 UI 存在且处于可出牌模式 |

控制器据此等待"已结算状态"，而不是靠 sleep 猜时间。

## 安装

1. **关闭游戏，备份存档。**
2. 从 [Releases](https://github.com/enderzcx/sts2-bridge/releases) 下载对应平台的包，按包内说明把 `STS2_MCP.dll` 和 `STS2_MCP.json` 放进 Mod 目录。
3. 从 Steam 启动，确认首次 Mod 提示后重启。
4. 验证：

```sh
curl --noproxy '*' http://127.0.0.1:15526/
# {"message":"Hello from STS2 MCP v0.4.0","status":"ok"}
```

Mod 只绑定本机回环地址，不对外监听。**不要把端口转发到公网。**

## 从源码构建

需要 .NET 9 SDK、Git 和本机游戏程序集。

```sh
export STS2_GAME_DIR='/path/to/SteamLibrary/steamapps/common/Slay the Spire 2'
dotnet build STS2_MCP.csproj -c Release -o out "-p:STS2GameDir=$STS2_GAME_DIR"
cp mod_manifest.json out/STS2_MCP.json
```

## 与本仓库的关系

```
上游 STS2MCP (Gennadiyev, MIT)
   └─ 本 fork: + 动作就绪字段
        ↓ HTTP JSON, 127.0.0.1:15526
      spire-ctl（控制器：状态契约 / 合法性 / 校验执行）
```

本仓库只含 Mod 源码与补丁，**不包含**游戏程序集、游戏素材、个人存档或凭据。

## 兼容性

在 **macOS arm64、游戏 v0.107.1** 上验证。上游 API 随游戏版本变化，其他版本请自行验证；`ready_for_action` 依赖的字段若在新版本改名，控制器会明确报错而不是静默继续。

## 上游文档

完整的 API 参考、多人模式与 MCP server 说明见 [UPSTREAM-README.md](UPSTREAM-README.md)
（上游在 v0.103.2 上测试，本 fork 固定并验证的是 v0.107.1）。

## 许可

MIT。上游版权归 Yikun Ji（见 [LICENSE](LICENSE)）；本 fork 的改动同样以 MIT 发布。
