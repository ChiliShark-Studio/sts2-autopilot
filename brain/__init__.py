"""sts2-autopilot 的决策层.

分层参考 AttemorySystem/spire-agent, 并适配 HTTP 桥接:

    contracts  请求和决策的数据结构, 使用便于移植到 C# 的 JSON 形状
    ports      GamePort / ToolPort 接口边界
    router     界面到决策单元的路由
    agents     地图 / 构筑 / 战斗决策单元及游戏循环
    combat     战斗搜索、临时选牌、共享结算、敌人规则、药水顾问与真实战斗会话
    map        地图规划、路线校验与风险复核
    build      构筑界面分派、商店决策、永久选牌与共用评分
    strategy   本局上下文、角色提示、局面输入与模型候选选择
    transport  实时 HTTP 桥接
    replay     支持离线回放的 JSONL 日志
"""

__all__ = [
    "actions",
    "agents",
    "build",
    "cli",
    "combat",
    "contracts",
    "effects",
    "factory",
    "llm",
    "map",
    "model",
    "ports",
    "replay",
    "router",
    "strategy",
    "transport",
]
