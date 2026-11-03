"""M4 智能体调用与会话记忆（对齐 openapi `/agent/invoke` 契约）+ M7 会话管理与计量落账。

- POST   /agent/invoke           多轮编排（LangGraph retrieve→generate）+ 会话记忆 + 计量
- GET    /agent/sessions         会话列表（默认只看自己的；平台管理员可 all=true 看全量）
- GET    /agent/sessions/{id}    会话记忆查询（仅创建者本人）
- DELETE /agent/sessions/{id}    删除会话（含 PG 副本，防重启复活）
M7 计量：每次调用前校验部门配额（超限 429），调用后按真实 token 落账并回显本单成本。
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import AgentInvokeRequest
from app.routers.agents import _AGENTS
from app.services import agent_runtime, audit, session_store, usage_store
router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/invoke")
async def invoke_agent(req: AgentInvokeRequest, caller: CallerContext = Depends(get_caller),
                      trace_id: str = Depends(get_trace_id)):
    agent = _AGENTS.get(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"智能体不存在：{req.agent_id}")
    # M5 红线：评审通过（published）才可调用
    if agent.get("status") != "published":
        raise HTTPException(status_code=403,
                            detail=f"智能体未发布（status={agent.get('status')}）：评审通过后才可调用（第35章红线）")
    # M7 配额红线：本部门滚动窗口 token 超限直接拦截（计费是红线，不只是报表）
    try:
        usage_store.enforce_quota(caller)
    except usage_store.QuotaExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=(f"quota exceeded: 部门 {e.used}/{e.quota} tokens（{usage_store.WINDOW_DAYS} 天）"
                    f"，请联系管理员提额或改用私有化模型"),
        )
    try:
        sess = session_store.get_or_create(req.agent_id, caller, req.session_id)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在或不属于该智能体：{req.session_id}")

    history = session_store.history_of(sess["session_id"])
    t0 = time.time()
    # M15：把该智能体（技能包）的执行提示词与适配器类型交给运行时，不再全局共用默认助手
    result = agent_runtime.run_agent(
        req.agent_id, req.input, caller, history=history,
        system_prompt=agent.get("system_prompt"),
        adapter_type=agent.get("adapter_type", ""),
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    # 记忆落账：用户轮 + 助手轮
    session_store.append_turn(sess["session_id"], "user", req.input)
    session_store.append_turn(sess["session_id"], "assistant", result["answer"])

    # M7 计量落账：token 优先取网关真值，成本按模型单价固化到这一单
    u = result.get("usage") or {}
    model_id = u.get("model_id") or agent.get("model_id") or settings.LLM_DEFAULT_MODEL
    rec = usage_store.record(
        caller, req.agent_id, model_id,
        usage=u.get("usage") or {},
        real=bool(u.get("real")),
        latency_ms=u.get("latency_ms") or elapsed_ms,
        prompt_text=u.get("prompt_text") or req.input,
        completion_text=u.get("completion_text") or result["answer"],
    )
    return {
        "code": 0,
        "data": {
            "answer": result["answer"],
            "citations": result["citations"],
            "tool_calls": result["tool_calls"],
            "agent": {"agent_id": req.agent_id, "name": agent.get("name"),
                      "adapter_type": agent.get("adapter_type"),
                      "skill_applied": result.get("skill_applied", False)},
            "session": {
                "session_id": sess["session_id"],
                "turn_count": len(history) + 2,  # 本轮 user+assistant 已入账
                "query_rewritten": result.get("query_rewritten", False),
                "turn_no": result.get("turn_no", 1),
            },
            "usage": {
                "model_id": rec["model_id"],
                "prompt_tokens": rec["prompt_tokens"],
                "completion_tokens": rec["completion_tokens"],
                "total_tokens": rec["prompt_tokens"] + rec["completion_tokens"],
                "cost": rec["cost"],
                "currency": "CNY",
                "real": rec["real"],
                "latency_ms": rec["latency_ms"],
            },
            "quota": usage_store.quota_status(caller),
        },
    }
    audit.record("generate", caller, target=req.agent_id, result="ok",
                 meta={"session_id": sess["session_id"], "turn_no": result.get("turn_no", 1),
                       "model_id": rec["model_id"], "real": rec["real"]},
                 trace_id=trace_id)


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=200),
    all_users: bool = Query(False, alias="all"),
    caller: CallerContext = Depends(get_caller),
):
    """会话列表：默认仅本人；`all=true` 仅平台管理员可用（运维排查用）。"""
    if all_users and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可查看全量会话")
    items = session_store.list_sessions(caller, include_all=all_users, limit=limit)
    return {"code": 0, "data": {"sessions": items, "count": len(items)}}


@router.get("/sessions/{session_id}")
async def get_session_memory(session_id: str, caller: CallerContext = Depends(get_caller)):
    """查看会话记忆（仅会话创建者本人，第18章权限语义延伸到会话）。"""
    try:
        sess = session_store.get_session(session_id, caller)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return {"code": 0, "data": {**session_store.session_summary(sess),
                                "context_preview": session_store.build_context(sess)}}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, caller: CallerContext = Depends(get_caller)):
    try:
        session_store.delete_session(session_id, caller)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return {"code": 0, "data": {"session_id": session_id, "deleted": True}}
