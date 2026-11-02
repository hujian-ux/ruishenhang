"""M4 智能体调用与会话记忆（对齐 openapi `/agent/invoke` 契约）。

- POST /agent/invoke：多轮编排（LangGraph retrieve→generate）+ 会话记忆
- GET  /agent/sessions/{session_id}：会话记忆查询（仅创建者本人）
会话为工程态扩展：session_id 传入续聊，缺省新建；归属/同智能体校验。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import AgentInvokeRequest
from app.routers.agents import _AGENTS
from app.services import agent_runtime, session_store

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/invoke")
async def invoke_agent(req: AgentInvokeRequest, caller: CallerContext = Depends(get_caller)):
    agent = _AGENTS.get(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"智能体不存在：{req.agent_id}")
    # M5 红线：评审通过（published）才可调用
    if agent.get("status") != "published":
        raise HTTPException(status_code=403,
                            detail=f"智能体未发布（status={agent.get('status')}）：评审通过后才可调用（第35章红线）")
    try:
        sess = session_store.get_or_create(req.agent_id, caller, req.session_id)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在或不属于该智能体：{req.session_id}")

    history = session_store.history_of(sess["session_id"])
    result = agent_runtime.run_agent(req.agent_id, req.input, caller, history=history)
    # 记忆落账：用户轮 + 助手轮
    session_store.append_turn(sess["session_id"], "user", req.input)
    session_store.append_turn(sess["session_id"], "assistant", result["answer"])
    return {
        "code": 0,
        "data": {
            "answer": result["answer"],
            "citations": result["citations"],
            "tool_calls": result["tool_calls"],
            "session": {
                "session_id": sess["session_id"],
                "turn_count": len(history) + 2,  # 本轮 user+assistant 已入账
                "query_rewritten": result.get("query_rewritten", False),
                "turn_no": result.get("turn_no", 1),
            },
        },
    }


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
