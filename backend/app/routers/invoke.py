"""M4 智能体调用与会话记忆（对齐 openapi `/agent/invoke` 契约）+ M7 会话管理与计量落账。

- POST   /agent/invoke           多轮编排（LangGraph retrieve→generate）+ 会话记忆 + 计量
- GET    /agent/sessions         会话列表（默认只看自己的；平台管理员可 all=true 看全量）
- GET    /agent/sessions/{id}    会话记忆查询（仅创建者本人）
- DELETE /agent/sessions/{id}    删除会话（含 PG 副本，防重启复活）
M7 计量：每次调用前校验部门配额（超限 429），调用后按真实 token 落账并回显本单成本。
"""
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import AgentInvokeRequest
from app.routers.agents import _AGENTS
from app.services import (agent_runtime, artifact, audit, file_store, session_store,
                          tabular, usage_store)

router = APIRouter(prefix="/agent", tags=["agent"])


def _load_attachments(file_ids: list[str], caller: CallerContext):
    """取附件并解析。越权与不存在一律 404（不给出可枚举的信号）。

    单份文件解析失败不阻断执行 —— 把错误文本写进上下文，让模型明确告诉用户
    "这份表读不出来、需要补什么"，比整个请求 500 有用得多。
    """
    out = []
    for fid in file_ids:
        rec = file_store.get(fid)
        if not rec or not file_store.can_access(rec, caller):
            raise HTTPException(status_code=404, detail=f"文件不存在或无权访问：{fid}")
        data = file_store.read(rec)
        if data is None:
            parsed = {"ok": False, "filename": rec.get("filename", fid),
                      "error": "文件内容已丢失（对象存储不可达或已被清理）"}
        else:
            parsed = tabular.parse(data, rec.get("filename", fid))
        out.append((rec, parsed))
    return out


def _build_artifact(caller: CallerContext, agent, req, result, attachments,
                    run_id: str, trace_id: str) -> dict | None:
    """产出物：答案 → xlsx → 对象存储 → 文件注册表。

    生成条件：有附件，或调用的是技能包（技能包的产出契约就是表格文件）。
    平台默认助手纯闲聊时不生成，避免存储里堆满 8KB 的"你好"。
    """
    is_skill = bool(agent.get("system_prompt")) and agent.get("system_prompt") != agent_runtime.SYSTEM_PROMPT
    if not attachments and not is_skill:
        return None

    input_files = [rec.get("filename") for rec, _ in attachments]
    summary = [["文件", "工作表", "行数", "列数", "列名（类型）"]]
    raw_sheet = None
    for rec, parsed in attachments:
        summary.extend(tabular.summarize_for_sheet(parsed)[1:])
    if len(attachments) == 1:
        raw_sheet = tabular.to_raw_sheet(attachments[0][1])

    u = result.get("usage") or {}
    data = artifact.build_xlsx(
        result["answer"],
        {
            "agent_name": agent.get("name") or req.agent_id,
            "agent_id": req.agent_id,
            "input_files": input_files,
            "model_id": u.get("model_id") or agent.get("model_id") or settings.LLM_DEFAULT_MODEL,
            "run_id": run_id,
            "trace_id": trace_id,
            "citations": [c.get("title") for c in result.get("citations") or []],
        },
        summary if attachments else None,
        raw_sheet,
    )
    fname = artifact.artifact_name(agent.get("name") or req.agent_id,
                                   input_files[0] if input_files else "")
    rec = file_store.put(data, fname, caller, kind=file_store.KIND_ARTIFACT,
                         agent_id=req.agent_id, run_id=run_id, trace_id=trace_id,
                         meta={"input_files": input_files,
                               "table_count": len(artifact.extract_tables(result["answer"])),
                               "skill_applied": bool(is_skill)})
    return file_store.public_view(rec)


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
    # M16：附件先解析成结构化上下文（表格画像 + 样本），再交给运行时
    attachments = _load_attachments(req.file_ids, caller) if req.file_ids else []
    file_context = ""
    if attachments:
        # 多附件时均分预算，避免第一份大表把后面的全挤掉
        budget = max(1500, tabular.PROMPT_CHAR_BUDGET // len(attachments))
        file_context = "\n\n".join(tabular.to_prompt_context(p, budget) for _, p in attachments)

    run_id = "run_" + uuid.uuid4().hex[:12]
    t0 = time.time()
    # M15：把该智能体（技能包）的执行提示词与适配器类型交给运行时，不再全局共用默认助手
    result = agent_runtime.run_agent(
        req.agent_id, req.input, caller, history=history,
        system_prompt=agent.get("system_prompt"),
        adapter_type=agent.get("adapter_type", ""),
        file_context=file_context,
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
    # M16：产出物 xlsx（技能包/带附件时生成），随后回下载地址
    artifact_view = _build_artifact(caller, agent, req, result, attachments, run_id, trace_id)
    tool_calls = result["tool_calls"] + ([{"tool": "file_parse", "authorized": True}]
                                         if attachments else [])

    payload = {
        "answer": result["answer"],
        "citations": result["citations"],
        "tool_calls": tool_calls,
        "run_id": run_id,
        "files_used": [{
            "file_id": frec["file_id"],
            "filename": frec.get("filename"),
            "parse_ok": parsed.get("ok", False),
            "parse_error": parsed.get("error", "") if not parsed.get("ok") else "",
            "sheets": parsed.get("sheet_count", 0),
            "rows": parsed.get("total_rows", 0),
        } for frec, parsed in attachments],
        "artifacts": [artifact_view] if artifact_view else [],
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
    }
    # M13 审计：必须在 return 之前 —— 原先写在 return 之后是不可达代码，
    # 导致「生成」这一类审计事件从未落库（M13 单测直接调 audit.record 没暴露）。
    audit.record("generate", caller, target=req.agent_id, result="ok",
                 meta={"session_id": sess["session_id"], "turn_no": result.get("turn_no", 1),
                       "model_id": rec["model_id"], "real": rec["real"], "run_id": run_id,
                       "files": [r["file_id"] for r, _ in attachments],
                       "artifact": artifact_view["file_id"] if artifact_view else ""},
                 trace_id=trace_id)
    return {"code": 0, "data": payload}


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
