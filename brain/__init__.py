"""Decision layer for sts2-autopilot.

Layering (mirrors AttemorySystem/spire-agent, adapted to an HTTP bridge):

    contracts  request/decision, JSON-shaped so a C# port can mirror it
    ports      GamePort / ToolPort -- the seams
    router     screen -> decision owner
    agents     map / build / combat owners + the game loop
    tools      map_router (LLM), combat_search (beam), build_flow (rewards)
    transport  live HTTP bridge
    replay     JSONL journal, replayable offline
"""

__all__ = [
    "actions",
    "agents",
    "cli",
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
