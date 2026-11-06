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
from app.services import (agent_runtime, artifact, audit, degrade, feedback, file_store,
                          guardrails, model_gateway, model_router, semantic_cache,
                          session_store, tabular, usage_store)

router = APIRouter(prefix="/agent", tags=["agent"])


def _evidence_text(result: dict) -> str:
    """把内部引用片段与公网摘要拼成可字面核对的证据文本（第39章声明-证据比对用）。"""
    parts: list[str] = []
    for c in result.get("citations") or []:
        parts.append(str(c.get("title") or ""))
        parts.append(str(c.get("snippet") or ""))
        parts.extend(str(s) for s in (c.get("snippets") or []))
    for w in result.get("web_sources") or []:
        parts.append(str(w.get("snippet") or ""))
    return "\n".join(p for p in parts if p)


def _m27_postprocess(req, agent, result, caller, run_id, elapsed_ms, input_guard):
    """M27 旁路：安全扫描 / 证据核对 / 缓存观测 / 路由建议 / 置信与质量埋点。

    关键取舍：**不改写 result["answer"]**。脱敏后的文本只作为建议放在响应里，
    否则会动到 M4/M17/M25 一系列依赖答案原文的断言。严格阻断由 GUARDRAILS_STRICT
    在调用前决定，这里只做「看得见、可追溯」的观测层。
    """
    out: dict = {"guardrails": {}, "cache": {}, "routing": {}, "confidence": None,
                 "handoff": None, "quality_recorded": False, "errors": []}
    try:
        evidence = _evidence_text(result)
        g = guardrails.scan_output(result["answer"], caller=caller,
                                   hits=result.get("citations") or [], evidence=evidence)
        g["grounding"] = (guardrails.grounding_check(result["answer"], evidence)
                          if evidence else
                          {"checked": 0, "unsupported": [], "score": 1.0,
                           "skipped": "no_evidence_in_this_turn"})
        g["input"] = {"blocked": bool(input_guard.get("blocked")),
                      "hits": len(input_guard.get("hits") or []),
                      "risk": input_guard.get("risk", 0.0)}
        out["guardrails"] = g
    except Exception as exc:
        out["errors"].append(f"guardrails: {type(exc).__name__}: {exc}")

    try:
        cached = semantic_cache.get(req.input, req.agent_id)
        if not cached:
            semantic_cache.put(req.input, result["answer"], req.agent_id,
                               {"run_id": run_id, "real": bool((result.get("usage") or {}).get("real"))})
        out["cache"] = {"hit": bool(cached), "applied": False,
                        "note": "只做命中率观测，stub 结果不复用", **semantic_cache.stats()}
    except Exception as exc:
        out["errors"].append(f"semantic_cache: {type(exc).__name__}: {exc}")

    try:
        out["routing"] = {**model_router.route(req.input, agent), "applied": False}
    except Exception as exc:
        out["errors"].append(f"model_router: {type(exc).__name__}: {exc}")

    try:
        u = result.get("usage") or {}
        conf = degrade.confidence(result["answer"], _evidence_text(result),
                                  bool(u.get("real")), out.get("guardrails") or {})
        out["confidence"] = conf
        feedback.record_quality({
            "success": True, "real": bool(u.get("real")), "latency_ms": elapsed_ms,
            "hallucination_flag": bool((out["guardrails"].get("grounding") or {}).get("unsupported")),
            "handoff": False, "agent_id": req.agent_id,
            "userId": caller.userId, "deptId": caller.deptId, "confidence": conf,
        })
        out["quality_recorded"] = True
        if settings.HANDOFF_AUTO and conf < settings.CONFIDENCE_THRESHOLD:
            out["handoff"] = degrade.handoff(
                run_id, req.agent_id, f"置信 {conf:.2f} 低于阈值 {settings.CONFIDENCE_THRESHOLD}",
                {"input": req.input[:300], "answer": result["answer"][:500]}, caller)
    except Exception as exc:
        out["errors"].append(f"degrade/feedback: {type(exc).__name__}: {exc}")
    return out


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


def _build_artifacts(caller: CallerContext, agent, req, result, attachments,
                     run_id: str, trace_id: str) -> list[dict]:
    """产出物：答案 → 多格式文件 → 对象存储 → 文件注册表。

    生成条件：有附件，或调用的是技能包（技能包的产出契约就是文件）。
    平台默认助手纯闲聊时不生成，避免存储里堆满 8KB 的"你好"。

    M17：一次调用可同时出 xlsx/docx/pptx/html/pdf。**模型只调一次**，
    多格式只是同一份答案的不同排版 —— 重复调模型既慢又费 token。
    单个格式渲染失败不拖垮整单：记在 failures 里，其余格式照常返回。
    """
    is_skill = bool(agent.get("system_prompt")) and agent.get("system_prompt") != agent_runtime.SYSTEM_PROMPT
    if not attachments and not is_skill:
        return []

    formats = artifact.normalize_formats(req.output_formats,
                                         agent.get("outputs_contract", ""))
    input_files = [rec.get("filename") for rec, _ in attachments]
    summary = [["文件", "工作表", "行数", "列数", "列名（类型）"]]
    raw_sheet = None
    for rec, parsed in attachments:
        summary.extend(tabular.summarize_for_sheet(parsed)[1:])
    if len(attachments) == 1:
        raw_sheet = tabular.to_raw_sheet(attachments[0][1])

    u = result.get("usage") or {}
    meta = {
        "agent_name": agent.get("name") or req.agent_id,
        "agent_id": req.agent_id,
        "input_files": input_files,
        "model_id": u.get("model_id") or agent.get("model_id") or settings.LLM_DEFAULT_MODEL,
        "run_id": run_id,
        "trace_id": trace_id,
        "citations": [c.get("title") for c in result.get("citations") or []],
    }
    base = agent.get("name") or req.agent_id
    src = input_files[0] if input_files else ""

    out: list[dict] = []
    for fmt in formats:
        try:
            data = artifact.build(fmt, result["answer"], meta,
                                  summary if attachments else None, raw_sheet)
        except Exception as exc:      # 单格式失败不影响其他格式
            result.setdefault("artifact_errors", []).append(f"{fmt}: {type(exc).__name__}: {exc}")
            continue
        fname = artifact.artifact_name(base, src, fmt)
        rec = file_store.put(data, fname, caller, kind=file_store.KIND_ARTIFACT,
                             agent_id=req.agent_id, run_id=run_id, trace_id=trace_id,
                             meta={"format": fmt,
                                   "input_files": input_files,
                                   "table_count": len(artifact.extract_tables(result["answer"])),
                                   "skill_applied": bool(is_skill)})
        out.append(file_store.public_view(rec))
    return out


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
    # M27 第27章：输入侧护栏。默认只记录；GUARDRAILS_STRICT=1 且确实命中注入模式才拦截，
    # 避免「严格模式把正常问题也全拦掉」这种把安全做成不可用的写法。
    input_guard: dict = {"blocked": False, "hits": [], "risk": 0.0}
    if settings.GUARDRAILS_ENABLED:
        try:
            input_guard = guardrails.scan_input(req.input)
        except Exception:
            input_guard = {"blocked": False, "hits": [], "risk": 0.0,
                           "error": "guard_unavailable"}
        if (settings.GUARDRAILS_STRICT and input_guard.get("blocked")
                and input_guard.get("hits")):
            audit.record("generate", caller, target=req.agent_id,
                         result="blocked_injection",
                         meta={"run_id": run_id, "hits": len(input_guard["hits"])},
                         trace_id=trace_id)
            raise HTTPException(status_code=400, detail="输入命中提示注入 / 越权指令护栏，已拒绝执行")
    t0 = time.time()
    # M15：把该智能体（技能包）的执行提示词与适配器类型交给运行时，不再全局共用默认助手
    try:
        result = agent_runtime.run_agent(
            req.agent_id, req.input, caller, history=history,
            system_prompt=agent.get("system_prompt"),
            adapter_type=agent.get("adapter_type", ""),
            file_context=file_context,
            # M26：技能定义可要求抓取前 N 篇网页正文（竞品调研），其余只要摘要
            web_fetch_top=int(agent.get("web_fetch_top") or 0),
        )
    except model_gateway.LLMUnavailable as exc:
        # M17：LLM_STRICT=1 时真模型失败直接 502，绝不拿 stub 冒充结果 ——
        # 「链路通了」和「效果达标」是两件事，静默回退会让人把前者当后者。
        audit.record("generate", caller, target=req.agent_id, result="llm_error",
                     meta={"run_id": run_id, "error": str(exc)[:300]}, trace_id=trace_id)
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}")
    elapsed_ms = int((time.time() - t0) * 1000)
    # M27：安全扫描 / 证据核对 / 缓存观测 / 路由建议 / 置信 / 质量埋点（旁路，不改答案）
    m27 = _m27_postprocess(req, agent, result, caller, run_id, elapsed_ms, input_guard)

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
    # M16/M17：产出物（技能包/带附件时生成），多格式一次出齐
    artifact_views = _build_artifacts(caller, agent, req, result, attachments, run_id, trace_id)
    # 这一轮「到底走没走真模型」要跟着轮次一起存：
    # 只存在当次响应里的话，刷新一次页面，"这是 stub 结果"的提示就消失了，
    # 用户第二天翻记录会把占位内容当成模型结论。
    extra = {"llm_real": bool(u.get("real")),
             "web_sources": result.get("web_sources") or [],
             "web_search": result.get("web_search"),
             # M27：置信与护栏摘要也挂轮次，翻历史时仍能看出「这轮是低置信/被标了什么」
             "confidence": m27.get("confidence"),
             "guardrails": {"input_risk": ((m27.get("guardrails") or {}).get("input") or {}).get("risk"),
                            "flags": (m27.get("guardrails") or {}).get("flags", []),
                            "leaks": (m27.get("guardrails") or {}).get("leaks", [])}}
    if artifact_views:
        # 产出物挂到这一轮上：刷新页面 / 换设备打开历史会话时仍能下载
        extra.update({"artifacts": artifact_views, "run_id": run_id})
    session_store.attach_meta(sess["session_id"], "assistant", extra)
    tool_calls = result["tool_calls"] + ([{"tool": "file_parse", "authorized": True}]
                                         if attachments else [])

    payload = {
        "answer": result["answer"],
        "citations": result["citations"],
        # M26：公网来源与内部引用分开回传，前端不要把两者拼成一个「来源」列表
        "web_sources": result.get("web_sources") or [],
        "web_search": result.get("web_search"),
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
        "artifacts": artifact_views,
        "artifact_errors": result.get("artifact_errors") or [],
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
        # M17：把「这次到底是真模型还是 stub」摆在响应顶层。
        # 之前只隐含在 usage.real 里，前端与验收脚本都容易漏看。
        "llm": {
            "real": bool(u.get("real")),
            "provider": model_gateway.settings.LLM_PROVIDER,
            "error": u.get("error") or "",
        },
        "output_formats": [a["meta"].get("format") for a in artifact_views],
        "quota": usage_store.quota_status(caller),
        # ── M27 观测层：全部是「看得见」的附加信息，不改动上面任何既有字段 ──
        "guardrails": m27.get("guardrails") or {},
        "confidence": m27.get("confidence"),
        "cache": m27.get("cache") or {},
        "routing": m27.get("routing") or {},
        "handoff": m27.get("handoff"),
        "m27_errors": m27.get("errors") or [],
    }
    # M13 审计：必须在 return 之前 —— 原先写在 return 之后是不可达代码，
    # 导致「生成」这一类审计事件从未落库（M13 单测直接调 audit.record 没暴露）。
    audit.record("generate", caller, target=req.agent_id, result="ok",
                 meta={"session_id": sess["session_id"], "turn_no": result.get("turn_no", 1),
                       "model_id": rec["model_id"], "real": rec["real"], "run_id": run_id,
                       "files": [r["file_id"] for r, _ in attachments],
                       "formats": payload["output_formats"],
                       "artifact": artifact_views[0]["file_id"] if artifact_views else ""},
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
                                # 对外界面要能点开历史对话继续聊，所以回完整轮次；
                                # context_preview 仍保留——它才是真正进模型的那段（截断后）
                                "turns": [{k: v for k, v in t.items()
                                          if k in ("role", "content", "ts", "artifacts",
                                                   "run_id", "llm_real", "confidence",
                                                   "guardrails", "web_sources")}
                                          for t in sess["turns"]],
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
