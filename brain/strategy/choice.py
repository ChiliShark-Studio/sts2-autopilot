"""模型候选选择：供构筑和选牌调用，组装请求并校验候选编号与动作合法性。

负责回复重试、记录构筑计划和生成决策；不向游戏发送动作，模型连续失败时由调用方决定停止。
常规战斗出牌不调用此模块中的模型选择。
"""

from __future__ import annotations

import json

from ..actions import Action, ActionError
from ..contracts import Decision, Source
from ..effects import humanize
from ..model import GameState
from .context import StrategyContext
from .prompts import character_prompt
from .situation import situation


# 模型回复无效时的重试次数; 用尽后停下等待排查, 不再退回启发式.
LLM_ATTEMPTS = 3


def no_decision(tool: str, reason: str) -> Decision:
    """模型没有给出可用决策: 停下, 而不是猜一个动作."""
    return Decision(action=None, reason=reason, source=Source.NONE, tool=tool)


def combat_selection(state: GameState) -> bool:
    # 桥接的网格覆盖层可能没有 battle；hand 仅在实际战斗中提供。
    return state.battle is not None or "hand" in (state.raw.get("player") or {})


def choose(client, state: GameState, context: StrategyContext, *, task: str,
           candidates: list[tuple[int, Action, dict]], tool: str,
           allow_combat: bool = False, attempts: int = 1,
           notes: dict | None = None) -> Decision | None:
    """LLM 只能返回现有候选编号，不直接执行它生成的任意动作。

    ``allow_combat`` 只用于"战斗内无法模拟的药水"这类必须由模型判断的情况；
    常规出牌决策仍然不得调用模型。
    ``attempts`` 用于模型回复无效时重试；用尽后返回 None，由调用方决定停下。
    ``notes`` 是补充说明 (例如"哪些商品买不了、为什么"), 会一起进 payload,
    避免模型反复选择根本不可用的项 (商店删牌售罄时踩过).
    """
    if client is None or not candidates or (combat_selection(state) and not allow_combat):
        return None
    system = f"""你是《杀戮尖塔2》的选路与构筑助手。{task}
{character_prompt(state)}
根据最新局面重新检查已有组件、主要短板和下一步需求，之前的构筑计划仅供参考。
买卡、买遗物、删牌和留钱需要比较整体收益；药水需要考虑槽位与后续战斗。
路线要服务于构筑、恢复和资源需求，只分析可见地图，不假定未知节点的具体敌人。
效果以输入的当前游戏描述为准；尚未接入模拟器或经版本核实的攻略，不编造规则、精确胜率或已模拟的结论。
只能选择 candidates 中的 choice 整数，每次仅执行一个动作，随后重新读取状态。
只输出 JSON：{{"choice": <候选整数>, "reason": "<简短中文依据>", "build_plan": "<已有组件、主要短板、下一步需求，300字以内>"}}"""
    payload = {
        "situation": situation(state, context),
        "task": task,
        "candidates": [
            {"choice": index, "action": action.to_payload(), "details": details}
            for index, action, details in candidates
        ],
    }
    if notes:
        payload["notes"] = notes
    actions = {index: action for index, action, _ in candidates}
    note = ""
    for _ in range(max(1, attempts)):
        # 重试时把失败原因放进 payload, 不能拼在 JSON 后面: 调用方会直接解析 body.
        attempt_payload = dict(payload)
        if note:
            attempt_payload["previous_reply_invalid"] = note
        body = humanize(json.dumps(attempt_payload, ensure_ascii=False))
        reply = client.json_chat(system, body)
        if not isinstance(reply, dict) or type(reply.get("choice")) is not int:
            note = ("上一次回复不是合法的 JSON choice 整数，"
                    "必须只输出 {\"choice\": <候选整数>, ...}。")
            continue
        if reply["choice"] not in actions:
            note = (f"上一次返回的 choice={reply['choice']} 不在候选中"
                    f"（候选只有 {sorted(actions)}），必须从中重新选择。")
            continue
        action = actions[reply["choice"]]
        try:
            action.check(state)
        except ActionError as error:
            note = (f"上一次返回的 choice={reply['choice']} 当前不可执行"
                    f"（{error}），必须换一个候选。")
            continue
        plan = reply.get("build_plan")
        if isinstance(plan, str) and plan.strip():
            context.plan = plan.strip()[:1000]
        return Decision(
            action=action,
            reason=str(reply.get("reason") or "采用模型选择"),
            source=Source.LLM, tool=tool,
            extra={"llm": dict(client.last_meta or {}), "build_plan": context.plan},
        )
    return None
