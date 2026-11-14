"""M4 智能体调用与会话记忆（对齐 openapi `/agent/invoke` 契约）+ M7 会话管理与计量落账。

- POST   /agent/invoke           多轮编排（LangGraph retrieve→generate）+ 会话记忆 + 计量
- GET    /agent/sessions         会话列表（默认只看自己的；平台管理员可 all=true 看全量）
- GET    /agent/sessions/{id}    会话记忆查询（仅创建者本人）
- DELETE /agent/sessions/{id}    删除会话（含 PG 副本，防重启复活）
M7 计量：每次调用前校验部门配额（超限 429），调用后按真实 token 落账并回显本单成本。
"""
import asyncio
import asyncio
import asyncio
import json
import threading
import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import AgentInvokeRequest, ContextPreviewRequest
from app.routers.agents import _AGENTS
from app.services import (agent_runtime, artifact, audit, degrade, feedback, file_store,
                          guardrails, memory_store, model_gateway, model_router, semantic_cache,
                          session_store, skill_pack, tabular, usage_store, vision)
from app.tools import registry as tool_registry, sandbox as tool_sandbox  # P0-2 / P0-3

router = APIRouter(prefix="/agent", tags=["agent"])

# M52 试跑样例上限：够还原一次真实作业，又不至于让模型上下文被样例挤爆。
MAX_SAMPLE_FILES = 6
MAX_SAMPLE_BYTES = 8 * 1024 * 1024


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


def _m27_postprocess(req, agent, result, caller, run_id, elapsed_ms, input_guard,
                      served_from_cache=False, cached_entry=None):
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
        if cached_entry is None:
            cached_entry = semantic_cache.get(req.input, req.agent_id)
        # 带数据的轮次不进缓存：语义缓存只按问题文本做键，把「用 A 公司台账跑出的
        # 答案」复用给「B 公司同样问法」是数据泄露级错误。
        if not cached_entry and not (req.file_ids or getattr(req, "use_samples", False)):
            semantic_cache.put(req.input, result["answer"], req.agent_id,
                               {"run_id": run_id, "real": bool((result.get("usage") or {}).get("real"))})
        out["cache"] = {"hit": bool(cached_entry), "served": bool(served_from_cache),
                        "sim_mode": (cached_entry or {}).get("sim_mode"),
                        "applied": bool(served_from_cache),
                        "note": ("命中且为真实产出时复用答案（SEMANTIC_CACHE_SERVE=1）"
                                 if settings.SEMANTIC_CACHE_SERVE
                                 else "观测命中率中；复用答案需开 SEMANTIC_CACHE_SERVE"),
                        **semantic_cache.stats()}
    except Exception as exc:
        out["errors"].append(f"semantic_cache: {type(exc).__name__}: {exc}")

    try:
        dec = model_router.route(req.input, agent)
        # P2-40：路由已通过运行时 applied=true；优先用本次实际路由结果标注。
        if result.get("routed_tier"):
            dec["tier"] = result["routed_tier"]
            dec["model"] = result.get("routed_model") or dec["model"]
        dec["applied"] = bool(result.get("routed_tier"))
        out["routing"] = dec
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


# ── P1-10 多模态：图片分流 + 视觉上下文 ──────────────────────────────────────
def _split_file_ids(file_ids: list[str], caller: CallerContext):
    """把图片从普通附件里摘出来，返回 (table_ids, image_ids)。

    开关关闭时 image_ids 恒为空、所有 file_ids 原样走旧链路 —— 这是 P1-10
    「默认零行为变更」的关键。开关开启时，图片不再进 `tabular.parse`，
    改由视觉通道解读；无权访问的图片留在 table_ids，随后由 `_load_attachments`
    统一 404，不在这里泄露「文件存在但不属于你」的信号。
    """
    ids = list(file_ids or [])
    if not ids or not getattr(settings, "VISION_ENABLED", False):
        return ids, []
    table_ids: list[str] = []
    image_ids: list[str] = []
    for fid in ids:
        rec = file_store.get(fid)
        if (rec and vision.is_image(rec.get("filename") or "")
                and file_store.can_access(rec, caller)):
            image_ids.append(fid)
        else:
            table_ids.append(fid)
    return table_ids, image_ids


def _vision_disabled_info(reason: str = "disabled") -> dict:
    return {"enabled": bool(getattr(settings, "VISION_ENABLED", False)),
            "ok": False, "reason": reason, "real": False, "provider": "",
            "model": "", "images": [], "errors": [], "error": "", "elapsed_ms": 0}


def _vision_context(image_ids: list[str], caller: CallerContext, question: str):
    """返回 (注入 file_context 的文本, 响应里的 vision 观测字段)。

    成功注入识别结论，失败注入「缺料提示」——两种情况下主模型都不会把图片
    当成不存在的东西，也不会拿到伪造的解读。
    """
    if not image_ids:
        return "", _vision_disabled_info("no_images")
    try:
        r = vision.analyze(image_ids, caller, prompt=question)
    except Exception as exc:      # 通道异常不能把整单打成 500
        r = {"ok": False, "reason": "provider_error", "text": "", "real": False,
             "provider": "", "model": "", "images": [], "errors": [], "usage": {},
             "error": f"{type(exc).__name__}: {exc}"[:300], "elapsed_ms": 0}
    info = {
        "enabled": True, "ok": bool(r.get("ok")), "reason": r.get("reason") or "",
        "real": bool(r.get("real")), "provider": r.get("provider") or "",
        "model": r.get("model") or "", "images": r.get("images") or [],
        "errors": r.get("errors") or [], "error": r.get("error") or "",
        "elapsed_ms": r.get("elapsed_ms") or 0, "usage": r.get("usage") or {},
    }
    text = vision.build_context(r) or vision.build_failure_hint(r)
    return text, info


def _build_file_context(attachments: list) -> str:
    """表格附件的结构化上下文（M16 原逻辑，原样抽出便于复用）。"""
    if not attachments:
        return ""
    budget = max(1500, tabular.PROMPT_CHAR_BUDGET // len(attachments))
    ctx = "\n\n".join(tabular.to_prompt_context(p, budget) for _, p in attachments)
    names = [rec.get("filename") or "" for rec, _ in attachments]
    return (
        "【本次随附材料】" + "、".join(n for n in names if n) +
        f"（共 {len(names)} 份，均已解析，明细见下文各段）。"
        "核对输入缺口时只按这份清单判断：清单里已有的资料不得写成「未提供」。\n\n"
        + ctx)


def _office62_scan(agent, answer, artifact_views, caller, trace_id, req, result,
                  enforce_block: bool = True) -> None:
    """办公62 硬规则：扫描答案文本 + 生成的 docx/pptx/xlsx 文件正文，合并后判定。

    - 默认观测告警（OFFICE62_ENABLED=1）。
    - enforce_block=True 且 high 级违规时，直接拦截产出物（仅同步路径用；
      流式 SSE 已逐 token 发出，finalize 再拦无意义，流式调用传 False 仅观测）。
    - 纯旁路 + 双层异常隔离，绝不阻断主链路；文件正文抽取依赖 python-docx /
      python-pptx / openpyxl，缺失或解析失败则跳过该文件（graceful degrade）。
    """
    if not getattr(settings, "OFFICE62_ENABLED", True):
        return
    try:
        scope = guardrails.office62_scope_for_agent(agent)
        texts = [answer or ""]
        for av in (artifact_views or []):
            fid = av.get("file_id")
            if not fid:
                continue
            rec = file_store.get(fid)
            if not rec:
                continue
            data = file_store.read(rec)
            if not data:
                continue
            ext = (av.get("filename") or "").rsplit(".", 1)[-1].lower()
            body = guardrails.extract_office_text(data, ext)
            if body:
                texts.append(body)
        oa = guardrails.scan_office62_blob(texts, scope=scope)
        result["office62"] = oa
        if oa["violations"]:
            try:
                audit.record("office62_scan", caller, target=req.agent_id,
                             result="violation", trace_id=trace_id,
                             meta={"scope": scope, "count": oa["count"],
                                   "blocked": oa["blocked"],
                                   "severity_counts": oa["severity_counts"],
                                   "violations": oa["violations"][:20]})
            except Exception:
                pass
        if enforce_block and getattr(settings, "OFFICE62_ENFORCE", False) and oa["blocked"]:
            result["answer"] = (
                "⚠️ 产出物触发办公域硬规则红线，已拦截：\n"
                + "\n".join(f"[{v['id']}] {v['message']}"
                             for v in oa["violations"]
                             if v["severity"] == "high")
                + "\n请调整内容后重试。"
            )
            result["office62_blocked"] = True
        # 生产级命中率采集（opt-in，OFFICE62_CAPTURE）：把真实产出物文本落盘，
        # 供测量台离线用「当前规则」重扫，统计生产命中率与规则漂移。含生产文本，
        # 仅受控 QA 环境开启；单段文本截断到 20k 字符防体积膨胀。
        if getattr(settings, "OFFICE62_CAPTURE", False) and texts:
            try:
                import os as _os
                from datetime import datetime, timezone
                cap_path = (getattr(settings, "EMBED_CACHE_PATH", "data/embed_cache.db")
                            .replace("embed_cache.db", "office62_captures.jsonl"))
                _os.makedirs(_os.path.dirname(cap_path) or ".", exist_ok=True)
                cap_rec = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "agent_id": req.agent_id,
                    "default_format": (agent.get("default_format") or ""),
                    "scope": scope,
                    "texts": [t[:20000] for t in texts],
                    "scan_at_prod": {
                        "violations": oa["violations"],
                        "blocked": oa["blocked"],
                        "severity_counts": oa["severity_counts"],
                    },
                }
                with open(cap_path, "a", encoding="utf-8") as _cf:
                    _cf.write(json.dumps(cap_rec, ensure_ascii=False) + "\n")
            except Exception:
                pass
    except Exception:
        pass


def _build_artifacts(caller: CallerContext, agent, req, result, attachments,
                     run_id: str, trace_id: str,
                     session_id: str = "", q_index: int = 0) -> list[dict]:
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
        # P1-7 逻辑制品键：会话ID::问题序号::格式。问题序号=会话 user 轮数（steer 不增
        # user 轮，自然命中同键升版；全新问题 user 轮 +1 成新链）。无会话（理论上不会）留空。
        art_key = f"{session_id}::{q_index}::{fmt}" if session_id else ""
        rec = file_store.put(data, fname, caller, kind=file_store.KIND_ARTIFACT,
                             agent_id=req.agent_id, run_id=run_id, trace_id=trace_id,
                             artifact_key=art_key,
                             meta={"format": fmt,
                                   "input_files": input_files,
                                   "table_count": len(artifact.extract_tables(result["answer"])),
                                   "skill_applied": bool(is_skill)})
        out.append(file_store.public_view(rec))
    return out


def _load_skill_samples(agent: dict, caller: CallerContext):
    """M52：把技能包自带的「原始输入」样例挂成本次附件。

    为什么放在服务端做：115 个包里 113 个带样例（客户邮件、材料价格表、历史台账），
    但这些文件只躺在包目录里。用户在页面上没法自己拿到，于是每次试跑都停在
    「请提供输入清单」。让平台自己去包里取，试跑成本从「准备一套数据」降到一次点击。

    约束：只取 input 类样例、最多 6 份、总量 ≤8MB（样例是给人试跑用的，
    不是让模型吞整包）；单份读不出来就跳过，不阻断整单。
    """
    src = agent.get("source_path") or ""
    if not src:
        return [], []
    loaded, used, total = [], [], 0
    for s in skill_pack.input_samples(src, limit=MAX_SAMPLE_FILES):
        if total + int(s.get("size") or 0) > MAX_SAMPLE_BYTES:
            break
        data = skill_pack.sample_bytes(s["filename"], src)
        if not data:
            continue
        # 用 readme 里的中文原名入库：模型看到「历史报价.xlsx」才认得出这是输入清单
        # 里的那一项；拼音名它只会判成「未提供」然后回头问用户要。
        shown = s.get("display_name") or s["filename"]
        parsed = tabular.parse(data, shown)
        rec = file_store.put(
            data, shown, caller,
            agent_id=agent.get("agent_id") or "", meta={"sample": True, "skill_sample": True,
                                                        "source_file": s["filename"],
                                                        "parse_ok": parsed.get("ok", False)})
        loaded.append((rec, parsed))
        used.append({"filename": shown, "source_file": s["filename"], "kind": "sample_input",
                     "size": len(data), "parse_ok": bool(parsed.get("ok"))})
        total += len(data)
    return loaded, used


@router.post("/invoke")
async def invoke_agent(req: AgentInvokeRequest, caller: CallerContext = Depends(get_caller),
                      trace_id: str = Depends(get_trace_id)):
    agent = _AGENTS.get(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"智能体不存在：{req.agent_id}")
    # M43 多租户隔离：租户私有智能体，跨租户不可见、不可调用（与「无权=不存在」统一 404）。
    # 平台共享资产（tenant_id 留空）对所有租户可见；platform_admin 作为运营方可跨租户支持。
    _tid = agent.get("tenant_id")
    if _tid and _tid != caller.tenantId and caller.role != "platform_admin":
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

    # ── P1-5 会话串行化：同步接口同样互斥，避免与流式并发打穿 ──
    sid = sess["session_id"]
    lock = _session_lock(sid)
    if lock.locked():
        raise HTTPException(status_code=409,
                            detail="session_busy: 该会话正在生成中，请勿用同步接口并发调用（P1-5）")
    await lock.acquire()
    history = session_store.history_of(sid)
    # M16：附件先解析成结构化上下文（表格画像 + 样本），再交给运行时。
    # P1-10：图片先分流，不进 tabular.parse；识别结论在 safe_input 之后并入上下文。
    table_ids, image_ids = _split_file_ids(req.file_ids, caller)
    attachments = _load_attachments(table_ids, caller) if table_ids else []
    # M52：勾了「用样例数据试跑」就把技能包自带的原始输入一起挂上（用户自己能传，
    # 两路合并，不去重也不覆盖 —— 用户点上传说明他想用自己的数据比对。
    samples_used: list = []
    if req.use_samples:
        sample_att, samples_used = _load_skill_samples(agent, caller)
        attachments = attachments + sample_att
    file_context = _build_file_context(attachments)

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
    # M27/DNS：归一化后的文本喂给运行时（清零宽/同形/控制字符），防不可见字符注入绕过字面检测；
    # clean_text 缺失时回落原输入。必须放在 run_agent 之前，否则 safe_input 未绑定。
    safe_input = input_guard.get("clean_text") or req.input

    # P1-10 多模态：把视觉结论（或明确的「未产出结论」缺料提示）并入附件上下文。
    # 放在 safe_input 之后，视觉提问用的是归一化文本，不绕过输入护栏。
    vision_ctx, vision_info = _vision_context(image_ids, caller, safe_input)
    if vision_ctx:
        file_context = (file_context + "\n\n" + vision_ctx) if file_context else vision_ctx

    # P2-40 语义缓存：命中且为「真模型产出」时，按 SEMANTIC_CACHE_SERVE 开关复用答案，
    # 跳过 run_agent（省一次模型调用 + 检索）。默认关，先只观测命中率（零行为变更）。
    # P1-10：带图轮次同样不进缓存 —— 缓存键只有问题文本，把「A 公司图表」的结论
    # 复用给「B 公司同问法」是数据泄露级错误，与 M16 带数据轮次同一条红线。
    served_from_cache = False
    cached = None
    if (settings.SEMANTIC_CACHE_SERVE and settings.SEMANTIC_CACHE_ENABLED
            and not attachments and not image_ids):
        try:
            cached = semantic_cache.get(safe_input, req.agent_id)
            if cached and bool((cached.get("meta") or {}).get("real")):
                served_from_cache = True
                t0 = time.time()
                result = {
                    "answer": cached["answer"], "citations": [],
                    "web_sources": [], "web_search": None,
                    "query_rewritten": False, "turn_no": 0,
                    "usage": {"model_id": settings.LLM_DEFAULT_MODEL, "real": True,
                              "usage": {}, "latency_ms": 0,
                              "routed_tier": cached.get("tier"),
                              "routed_model": None},
                    "routed_tier": cached.get("tier"), "routed_model": None,
                    "tool_calls": [], "skill_applied": False,
                    "cached": True, "cache_sim_mode": cached.get("sim_mode"),
                }
                elapsed_ms = int((time.time() - t0) * 1000)
        except Exception:
            cached = None
            served_from_cache = False

    t0 = time.time()
    # M15：把该智能体（技能包）的执行提示词与适配器类型交给运行时，不再全局共用默认助手
    # P1 反馈 A/B（opt-in，默认关）：开启 AB_ENABLED 时，若 agent 在某 running 实验，
    # 按用户确定性分配变体 prompt 并拼接到 system_prompt；默认不启用，零行为变更。
    system_prompt = agent.get("system_prompt")
    ab_variant = None
    if getattr(settings, "AB_ENABLED", False):
        try:
            _exp = feedback.find_running_experiment(req.agent_id)
            if _exp is not None:
                _av = feedback.assign_variant(_exp, getattr(caller, "userId", "") or run_id)
                if _av and _av.get("prompt_override"):
                    system_prompt = (system_prompt or "") + "\n\n" + _av["prompt_override"]
                    ab_variant = _av["variant"]
        except Exception:
            ab_variant = None
    if not served_from_cache:
        try:
            result = agent_runtime.run_agent(
                req.agent_id, safe_input, caller, history=history,
                system_prompt=system_prompt,
                adapter_type=agent.get("adapter_type", ""),
                file_context=file_context,
                # M26：技能定义可要求抓取前 N 篇网页正文（竞品调研），其余只要摘要
                web_fetch_top=int(agent.get("web_fetch_top") or 0),
                # P0-2：真工具循环（默认 False，向后兼容）
                enable_tools=req.enable_tools,
                # P0 收尾2：把会话 ID 透传进工具循环，高危工具落单可回灌结果
                session_id=sess["session_id"],
                # P1-12：请求级近窗轮数覆盖（None → CTX_RUNTIME_TURNS）
                context_window=req.context_window,
            )
        except model_gateway.LLMUnavailable as exc:
            # M17：LLM_STRICT=1 时真模型失败直接 502，绝不拿 stub 冒充结果 ——
            # 「链路通了」和「效果达标」是两件事，静默回退会让人把前者当后者。
            audit.record("generate", caller, target=req.agent_id, result="llm_error",
                         meta={"run_id": run_id, "error": str(exc)[:300]}, trace_id=trace_id)
            lock.release()
            raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}")
    elapsed_ms = int((time.time() - t0) * 1000)
    result["answer"] = result.get("answer", "")
    result["ab_variant"] = ab_variant  # 命中的 A/B 变体（opt-in；默认 None）
    # M27 输出严格改写（opt-in）：GUARDRAILS_OUTPUT_ENFORCE=1 时把脱敏答案落回，
    # 真正拦截泄密。默认关，仅观测，不动答案原文（保护依赖答案的验收断言）。
    if getattr(settings, "GUARDRAILS_OUTPUT_ENFORCE", False):
        try:
            guarded = guardrails.apply_output_guard(
                result["answer"], caller=caller,
                hits=result.get("citations") or [],
                evidence=_evidence_text(result))
            result["answer"] = guarded["answer"]
        except Exception:
            pass
    # 办公62 硬规则扫描（M27 办公域治理）在产出物（含文件正文）生成后统一处理，
    # 见下方 _office62_scan 调用（同步路径 enforce_block=True，流式路径仅观测）。
    # M27：安全扫描 / 证据核对 / 缓存观测 / 路由建议 / 置信 / 质量埋点（旁路，不改答案）
    m27 = _m27_postprocess(req, agent, result, caller, run_id, elapsed_ms, input_guard,
                           served_from_cache=served_from_cache, cached_entry=cached)

    # P2-42 置信降级转人工（蓝图第 42 章）：默认关闭（HANDOFF_AUTO=0），仅观测。
    # 开启后，低置信结果自动建人工工单，但不改答案（人接着看），避免静默把低质答案当结论。
    if getattr(settings, "HANDOFF_AUTO", False):
        try:
            from app.services import degrade as degrade_svc
            ho = degrade_svc.maybe_handoff(
                run_id, req.agent_id,
                {"answer": result["answer"],
                 "evidence": _evidence_text(result),
                 "llm_real": bool(result.get("usage", {}).get("real")),
                 "guard": (result.get("guard_hits") or input_guard)},
                caller)
            if ho:
                result["handoff_id"] = ho.get("id")
        except Exception:
            pass

    # 记忆落账：用户轮 + 助手轮
    session_store.append_turn(sess["session_id"], "user", req.input)
    session_store.append_turn(sess["session_id"], "assistant", result["answer"])
    # P0-4：本轮对话抽取长期记忆（异步 fire，不阻塞响应；无模型时零开销跳过）
    _schedule_memory_extract(caller, req.input, result["answer"], req.agent_id, sess["session_id"])

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
    # P1-7：问题序号=会话 user 轮数（append_turn(user) 已入账，故此刻统计即得）
    q_index = sum(1 for t in sess["turns"] if t["role"] == "user")
    artifact_views = _build_artifacts(caller, agent, req, result, attachments, run_id, trace_id,
                                      session_id=sess["session_id"], q_index=q_index)
    # 办公62 硬规则：同步路径可拦截（enforce_block=True）
    _office62_scan(agent, result["answer"], artifact_views, caller, trace_id, req, result,
                  enforce_block=True)
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
        # M52：本次自动挂载了哪些包内样例（空列表 = 没开这个开关或包内没有样例）
        "samples_used": samples_used,
        # P1-10：视觉通道观测（默认 enabled=False / reason=disabled，老客户端忽略即可）
        "vision": vision_info,
        # P1-12：上下文窗口观测（纯计算，不触发压缩通道）
        "context": session_store.context_observation(sess, req.context_window),
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
        "llm_real": bool(u.get("real")),
        "llm": {
            "real": bool(u.get("real")),
            "provider": model_gateway.settings.LLM_PROVIDER,
            "error": u.get("error") or "",
            # M51：实际作答的模型 + 是否由轮换链换来的（额度耗尽时用得上）
            "model": u.get("model_id") or model_id,
            "rotated": bool(u.get("rotated")),
            "attempts": u.get("attempts") or [],
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
                       "images": image_ids,
                       "formats": payload["output_formats"],
                       "artifact": artifact_views[0]["file_id"] if artifact_views else ""},
                 trace_id=trace_id)
    lock.release()
    return {"code": 0, "data": payload}


# ── P0-1：会话流式化（SSE + 中断）──────────────────────────────────────────────
# 与 /agent/invoke 同口径（鉴权/配额/会话/附件/样例/护栏/记忆/计量/产出物），
# 区别只在传输层：答案以 token 增量推给前端，并支持 DELETE 中途取消。
ACTIVE_STREAMS: dict[str, dict] = {}   # request_id -> {"cancelled", "caller_id", "session_id"}

# ── P1-5 会话串行化（防并发打穿）──────────────────────────────────────────────
# 同一会话同一时刻只允许一条生成在跑；第二条要么 409 排队由前端重发，要么以 steer
# 形式取消当前流后重生成。锁键用 session_id —— 新会话无冲突，续聊会话严格互斥。
SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(sid: str) -> asyncio.Lock:
    return SESSION_LOCKS.setdefault(sid, asyncio.Lock())


def _schedule_memory_extract(caller, user_input, answer, agent_id, session_id):
    """P0-4：本轮对话抽取长期记忆。daemon 线程 fire-and-forget，不阻塞响应；
    模型不可用（llm_available=False）时 extract_into 内部零开销跳过，无副作用。"""
    try:
        from app.services import memory_store
        if not memory_store.enabled():
            return
        threading.Thread(
            target=memory_store.extract_into,
            args=(caller, user_input, answer, agent_id, session_id),
            daemon=True,
        ).start()
    except Exception:
        pass


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _finalize_stream(req, agent, caller, result, attachments, samples_used,
                    run_id, trace_id, input_guard, safe_input, sess, history,
                    steer_meta=None, image_ids=None) -> dict:
    """流式结束后落账：记忆 + 计量 + 产出物 + 审计（与 invoke_agent 同口径）。

    P1-5 steer：steer_meta 非 None 表示本次是「中途补充要求重生成」，
    不追加新的 user 轮（上一轮用户问题已在上下文中），只在助手轮记录约束。
    """
    if steer_meta is None:
        session_store.append_turn(sess["session_id"], "user", req.input)
    session_store.append_turn(sess["session_id"], "assistant", result["answer"])
    u = result.get("usage") or {}
    model_id = u.get("model_id") or agent.get("model_id") or settings.LLM_DEFAULT_MODEL
    rec = usage_store.record(
        caller, req.agent_id, model_id,
        usage=u.get("usage") or {},
        real=bool(u.get("real")),
        latency_ms=u.get("latency_ms") or 0,
        prompt_text=u.get("prompt_text") or safe_input,
        completion_text=u.get("completion_text") or result["answer"],
    )
    # P1-7：问题序号=会话 user 轮数（steer 不增 user 轮，故同问题再次生成命中同键升版）
    q_index = sum(1 for t in sess["turns"] if t["role"] == "user")
    artifact_views = _build_artifacts(caller, agent, req, result, attachments, run_id, trace_id,
                                      session_id=sess["session_id"], q_index=q_index)
    # 办公62 硬规则：流式答案已逐 token 发往前端，finalize 再拦无意义 → 仅观测（enforce_block=False）
    _office62_scan(agent, result["answer"], artifact_views, caller, trace_id, req, result,
                  enforce_block=False)
    extra = {"llm_real": bool(u.get("real")),
             "web_sources": result.get("web_sources") or [],
             "web_search": result.get("web_search"),
             "confidence": None,
             "guardrails": {"input_risk": (input_guard or {}).get("risk")}}
    if steer_meta:
        extra["steer_constraints"] = steer_meta
    if artifact_views:
        extra.update({"artifacts": artifact_views, "run_id": run_id})
    session_store.attach_meta(sess["session_id"], "assistant", extra)
    audit.record("generate", caller, target=req.agent_id, result="ok",
                 meta={"session_id": sess["session_id"], "turn_no": result.get("turn_no", 1),
                       "model_id": rec["model_id"], "real": rec["real"], "run_id": run_id,
                       "files": [r["file_id"] for r, _ in attachments],
                       "images": list(image_ids or []),
                       "formats": [a["meta"].get("format") for a in artifact_views],
                       "artifact": artifact_views[0]["file_id"] if artifact_views else ""},
                 trace_id=trace_id)
    return {
        "run_id": run_id,
        "session_id": sess["session_id"],
        # 与同步 /agent/invoke 同口径：完整 public_view 对象（含 file_id/filename/size/meta），
        # 前端预览与下载才拿得到真实文件名（只回 file_id 会让预览降级、下载存成无扩展名）。
        "artifacts": artifact_views,
        "usage": {"model_id": rec["model_id"], "total_tokens": rec["prompt_tokens"] + rec["completion_tokens"],
                  "cost": rec["cost"], "real": rec["real"], "latency_ms": rec["latency_ms"]},
    }


@router.post("/stream")
async def stream_agent(req: AgentInvokeRequest, caller: CallerContext = Depends(get_caller),
                     trace_id: str = Depends(get_trace_id)):
    """P0-1 流式调用：返回 text/event-stream。事件类型：
    meta（来源/工具）/ token（答案增量）/ done（完整结果 + 用量）/
    cancelled（被 DELETE 取消）/ error（模型不可达）/ finalized（落账结果：run_id/产出物/用量）。
    取消：DELETE /agent/stream/{request_id}。/agent/invoke 保持同步兼容不变。"""
    agent = _AGENTS.get(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"智能体不存在：{req.agent_id}")
    _tid = agent.get("tenant_id")
    if _tid and _tid != caller.tenantId and caller.role != "platform_admin":
        raise HTTPException(status_code=404, detail=f"智能体不存在：{req.agent_id}")
    if agent.get("status") != "published":
        raise HTTPException(status_code=403,
                            detail=f"智能体未发布（status={agent.get('status')}）：评审通过后才可调用（第35章红线）")
    try:
        usage_store.enforce_quota(caller)
    except usage_store.QuotaExceeded as e:
        raise HTTPException(status_code=429,
                            detail=f"quota exceeded: 部门 {e.used}/{e.quota} tokens（{usage_store.WINDOW_DAYS} 天）")
    try:
        sess = session_store.get_or_create(req.agent_id, caller, req.session_id)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在或不属于该智能体：{req.session_id}")

    # ── P1-5 会话串行化 + 中途追加约束 ──
    # steer：先取消本会话正在进行的任何流（若存在），再就合并后的输入重新生成；
    # 非 steer：会话正忙则直接 409，由前端排队重发，绝不并发打穿。
    sid = sess["session_id"]
    lock = _session_lock(sid)
    if req.steer:
        for rid, st in list(ACTIVE_STREAMS.items()):
            if st.get("session_id") == sid and st.get("caller_id") == caller.userId:
                st["cancelled"] = True
    elif lock.locked():
        raise HTTPException(
            status_code=409,
            detail="session_busy: 该会话正在生成中，请等待完成或点「停止」，"
                   "或发送补充要求以重新生成（P1-5）")

    # P1-10：与 /agent/invoke 同口径分流图片，两条生成路径行为必须一致。
    table_ids, image_ids = _split_file_ids(req.file_ids, caller)
    attachments = _load_attachments(table_ids, caller) if table_ids else []
    samples_used: list = []
    if req.use_samples:
        sample_att, samples_used = _load_skill_samples(agent, caller)
        attachments = attachments + sample_att
    file_context = _build_file_context(attachments)

    run_id = "run_" + uuid.uuid4().hex[:12]
    input_guard: dict = {"blocked": False, "hits": [], "risk": 0.0}
    if settings.GUARDRAILS_ENABLED:
        try:
            input_guard = guardrails.scan_input(req.input)
        except Exception:
            input_guard = {"blocked": False, "hits": [], "risk": 0.0, "error": "guard_unavailable"}
        if settings.GUARDRAILS_STRICT and input_guard.get("blocked") and input_guard.get("hits"):
            audit.record("generate", caller, target=req.agent_id, result="blocked_injection",
                         meta={"run_id": run_id}, trace_id=trace_id)
            lock.release()
            raise HTTPException(status_code=400, detail="输入命中提示注入 / 越权指令护栏，已拒绝执行")
    safe_input = input_guard.get("clean_text") or req.input
    # P1-10：视觉结论 / 缺料提示并入上下文，与同步接口同口径。
    vision_ctx, vision_info = _vision_context(image_ids, caller, safe_input)
    if vision_ctx:
        file_context = (file_context + "\n\n" + vision_ctx) if file_context else vision_ctx
    system_prompt = agent.get("system_prompt")

    request_id = "st_" + uuid.uuid4().hex[:12]
    ACTIVE_STREAMS[request_id] = {"cancelled": False, "caller_id": caller.userId,
                                  "session_id": sid}

    async def event_gen():
        # 会话级互斥：持有锁期间本会话不会再有第二条流在读/写 turns，天然串行。
        # steer 请求已在上方取消旧流，旧流释放锁后这里才能拿到；非 steer 在上方已 409。
        async with lock:
            history = session_store.history_of(sid)
            # P1-5 steer：把「上一轮用户问题 + 补充要求」并成新输入重生成助手答案，
            # 不新增 user 轮（避免对话上下文里出现两条重复提问）。
            if req.steer:
                last_user = next((t["content"] for t in reversed(history)
                                  if t["role"] == "user"), "")
                constraint = (req.steer_input or req.input or "").strip()
                effective_input = (f"{last_user}\n\n（用户中途补充要求：{constraint}）"
                                   if last_user else constraint)
                steer_meta = constraint
            else:
                effective_input = safe_input
                steer_meta = None
            final = None
            try:
                async for ev in agent_runtime.run_agent_stream(
                        req.agent_id, effective_input, caller, history=history,
                        system_prompt=system_prompt,
                        adapter_type=agent.get("adapter_type", ""),
                        file_context=file_context,
                        web_fetch_top=int(agent.get("web_fetch_top") or 0),
                        should_cancel=lambda: ACTIVE_STREAMS.get(request_id, {}).get("cancelled", False),
                        enable_tools=req.enable_tools,
                        # P0 收尾2：把会话 ID 透传进工具循环，高危工具落单可回灌结果
                        session_id=sid,
                        # P1-12：与同步路径同口径
                        context_window=req.context_window):
                    if ev["type"] == "done":
                        final = ev["result"]
                    yield _sse(ev)
                if ACTIVE_STREAMS.get(request_id, {}).get("cancelled"):
                    yield _sse({"type": "cancelled", "request_id": request_id,
                                "session_id": sid})
                    return
                if final:
                    fin = _finalize_stream(req, agent, caller, final, attachments, samples_used,
                                          run_id, trace_id, input_guard, safe_input, sess, history,
                                          steer_meta=steer_meta, image_ids=image_ids)
                    fin["vision"] = vision_info   # P1-10：与同步接口同口径的视觉观测
                    fin["context"] = session_store.context_observation(sess, req.context_window)
                    yield _sse({"type": "finalized", **fin})
                    # P0-4：本轮对话抽取长期记忆（异步 fire，不阻塞流）
                    _schedule_memory_extract(caller, effective_input, final["answer"], req.agent_id, sid)
            except model_gateway.LLMUnavailable as exc:
                yield _sse({"type": "error", "detail": f"模型调用失败：{exc}"})
            finally:
                ACTIVE_STREAMS.pop(request_id, None)

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "X-Request-Id": request_id})


@router.delete("/stream/{request_id}")
async def cancel_stream(request_id: str, caller: CallerContext = Depends(get_caller)):
    """P0-1 中途取消：置 cancelled 标志，流式生成器的 should_cancel 会在下一个 token 处中断。"""
    st = ACTIVE_STREAMS.get(request_id)
    if not st:
        return {"code": 0, "data": {"cancelled": False,
                                    "reason": "not_found_or_already_finished"}}
    st["cancelled"] = True
    return {"code": 0, "data": {"cancelled": True, "request_id": request_id}}


@router.get("/tools")
async def list_tools(caller: CallerContext = Depends(get_caller)):
    """P0-2 工具清单：返回当前沙箱规则下可用工具及其参数、风险等级。"""
    rules = tool_sandbox.load_rules()
    tools = tool_registry.list_tools(enabled_only=True, rules=rules)
    disabled = [n for n, c in (rules.get("tools", {}) or {}).items()
                if isinstance(c, dict) and c.get("enabled") is False]
    return {"code": 0, "data": {
        "tools": tools,
        "count": len(tools),
        "disabled": disabled,
        "shell_whitelist": (rules.get("shell", {}) or {}).get("command_whitelist", []),
        "workspace_root": rules.get("workspace_root"),
    }}


@router.get("/memory")
async def list_memory(caller: CallerContext = Depends(get_caller)):
    """P0 收尾3：跨会话长期记忆管理端点。列出当前 caller 可见的全部事实 + 统计。
    维度 scope 由 fact 的 user_id / dept_id 推断：个人 / 部门 / 租户共享。"""
    if not memory_store.enabled():
        raise HTTPException(status_code=409, detail="记忆功能未开启（MEMORY_ENABLED=0）")
    facts = memory_store.list_facts(caller)

    def _scope(f: dict) -> str:
        if not f.get("user_id"):
            return "tenant" if not f.get("dept_id") else "dept"
        return "user"

    items = [{
        "fact_id": f["fact_id"],
        "category": f.get("category"),
        "content": f.get("content"),
        "scope": _scope(f),
        "agent_id": f.get("agent_id") or None,
        "created_at": f.get("created_at"),
        "last_used": f.get("last_used"),
    } for f in facts]
    return {"code": 0, "data": {"count": len(items), "facts": items}}


@router.delete("/memory/{fact_id}")
async def delete_memory(fact_id: str, caller: CallerContext = Depends(get_caller)):
    """P0 收尾3：删除一条长期事实（仅同租户可删，防跨租户误删；软删，不破坏持久行）。"""
    ok = memory_store.forget(fact_id, caller)
    if not ok:
        raise HTTPException(status_code=404, detail=f"记忆不存在或无权删除：{fact_id}")
    return {"code": 0, "data": {"fact_id": fact_id, "deleted": True}}



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


# ── P1-12 上下文窗口：状态 + 预览（只读，不改任何会话）────────────────────────
@router.get("/context/status")
async def context_window_status(caller: CallerContext = Depends(get_caller)):
    """当前窗口配置快照。默认值 40/6/2 与改动前一致，压缩默认关。"""
    return {"code": 0, "data": {
        **session_store.window_config(),
        "note": "CTX_* 由环境变量 / .env.local 配置；单次请求可用 AgentInvokeRequest.context_window 覆盖",
        "legacy_aliases": {
            "MAX_SESSION_TURNS": session_store.MAX_SESSION_TURNS,
            "MAX_CONTEXT_TURNS": session_store.MAX_CONTEXT_TURNS,
        },
    }}


@router.get("/context/preview")
async def context_window_preview(
        session_id: str = Query(..., description="会话 ID（仅创建者本人 / 平台管理员）"),
        context_window: int | None = Query(None, ge=0, le=10000),
        compress: bool | None = Query(None, description="None=按配置 / true=强制开启 / false=强制关闭"),
        runtime: bool = Query(False, description="true=生成器口径(默认2轮) / false=会话详情口径(默认6轮)"),
        caller: CallerContext = Depends(get_caller)):
    """预览某会话真正会注入模型的上下文：近窗原文 + 历史摘要 + 轮数统计。"""
    try:
        sess = session_store.get_session(session_id, caller)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    detail = session_store.build_context_detail(
        sess, context_window=context_window, compress=compress, runtime=runtime)
    audit.record("context_preview", caller, target=session_id, result="ok",
                 meta={"window_turns": detail["window_turns"],
                       "dropped": detail["dropped_turns"],
                       "compress": compress, "runtime": runtime})
    return {"code": 0, "data": detail}


@router.post("/context/preview")
async def context_window_preview_custom(
        body: ContextPreviewRequest,
        caller: CallerContext = Depends(get_caller)):
    """离线试算：传自定义轮次看压缩效果，不读/写任何会话、不做归属校验。

    轮次与单轮字数都设了上限，避免有人拿预览端点当免费的超长文本处理器。
    """
    turns = [{"role": str(t.get("role") or "user"),
              "content": str(t.get("content") or "")[:4000]}
             for t in (body.turns or [])[:200]]
    detail = session_store.build_context_detail(
        {"turns": turns}, context_window=body.context_window,
        compress=body.compress, runtime=body.runtime)
    return {"code": 0, "data": detail}


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
                                                   "guardrails", "web_sources",
                                                   "steer_constraints")}
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


@router.post("/sessions/{session_id}/rollback")
async def rollback_session(session_id: str, caller: CallerContext = Depends(get_caller),
                           turn: int = Query(None, description="保留前 N 轮，其余丢弃；缺省回退一轮（删最后一轮）")):
    """P1-5 轮次回滚：截断会话到指定轮数，对话从上次断点继续（制品版本化不动，旧版本仍在文件库）。

    `turn` 为「保留前 N 轮」：turn=0 清空全部轮次，turn=len 等价不回滚。缺省回退一轮
    （删掉最后一轮，通常是一句未满意的助手回答，方便用户重新发问）。越界自动夹取到
    [0, len(turns)]。仅会话创建者本人可操作（沿用 get_session 隔离）。
    """
    try:
        sess = session_store.get_session(session_id, caller)
    except session_store.SessionAccessError:
        raise HTTPException(status_code=403, detail="会话不属于当前用户")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    n = len(sess["turns"])
    if turn is None:
        keep = max(0, n - 1)          # 默认回退一轮
    else:
        keep = max(0, min(int(turn), n))
    sess["turns"] = sess["turns"][:keep]
    session_store._persist(sess)      # 写穿 PG，重启不丢
    return {"code": 0, "data": {
        "session_id": session_id, "kept_turns": keep, "dropped_turns": n - keep,
        "remaining": len(sess["turns"])}}
