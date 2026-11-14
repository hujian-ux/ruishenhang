"""P1-9 · 把「专家委派」挂成主智能体可调用的 tool（#304）。

注册后 ``manifest_prompt()`` 会自动列出 ``delegate_expert``，凡开启 enable_tools
的主智能体都能把子任务委派给指定技能包；执行体走 ``subagents.dispatch`` 的同一套
可见域校验、限深、诚实降级与执行台账，不另起一条旁路。

延迟 import subagents 是刻意的：本模块在 ``app.tools`` 包导入期就被加载，
而 subagents 依赖 agent_store / model_gateway 等服务，延迟到 execute 时才导入
可彻底规避循环依赖。
"""
from __future__ import annotations

from typing import Any

from app.tools import registry

TOOL_NAME = "delegate_expert"


def _delegate(args: dict, caller: Any) -> dict:
    agent_id = str(args.get("agent_id") or "").strip()
    task = str(args.get("task") or "").strip()
    context = str(args.get("context") or "")
    if not agent_id or not task:
        return {"ok": False, "content": "", "error": "agent_id 与 task 均不能为空"}
    # 延迟导入，避免 tools 包导入期拉起整条服务链
    from app.services import subagents

    try:
        r = subagents.dispatch(
            agent_id, task, caller, context=context,
            parent_agent_id=str(args.get("parent_agent_id") or ""),
            depth=1, source="tool",       # 工具内委派：depth=1，再深由 MAX_DEPTH 拦
        )
    except Exception as exc:
        # HTTPException(404/400) 也在这里收口成结构化失败，回灌给工具循环当 observation
        detail = getattr(exc, "detail", None) or str(exc)
        return {"ok": False, "content": "", "error": f"delegate_denied: {detail}"[:300]}
    if not r.get("ok"):
        return {"ok": False, "content": "",
                "error": f"delegate_{r.get('status') or 'failed'}: {r.get('error') or ''}"[:300]}
    # 回灌内容截到工具 observation 的量级（loop._OBS_TRUNCATE=4000），避免挤爆上下文
    answer = (r.get("answer") or "")[:3500]
    header = f"【专家：{r.get('agent_name') or r.get('agent_id')}｜run_id={r.get('run_id')}】\n"
    return {"ok": True, "content": header + answer, "error": ""}


registry.register(
    TOOL_NAME,
    "把子任务委派给指定专家技能包独立执行（用它的人设/硬规则产出结论，不污染当前会话）",
    {"agent_id": "专家技能包 agent_id", "task": "要委派的子任务描述",
     "context": "可选：上游主任务背景或前序结论"},
    _delegate,
)
