"""sts2-autopilot 的决策层.

分层参考 AttemorySystem/spire-agent, 并适配 HTTP 桥接:

    contracts  请求和决策的数据结构, 使用便于移植到 C# 的 JSON 形状
    ports      GamePort / ToolPort 接口边界
    router     界面到决策单元的路由
    agents     地图 / 构筑 / 战斗决策单元及游戏循环
    combat     战斗搜索、共享结算、敌人规则、药水顾问与真实战斗会话
    tools      map_router (LLM), build_flow (奖励), select_tool (选牌)
    transport  实时 HTTP 桥接
    replay     支持离线回放的 JSONL 日志
"""

__all__ = [
    "actions",
    "agents",
    "cli",
    "combat",
    "contracts",
    "effects",
    "factory",
    "llm",
    "model",
    "ports",
    "replay",
    "router",
    "tools",
    "transport",
]
