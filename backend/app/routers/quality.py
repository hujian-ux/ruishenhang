"""M27 路由：评测质量 / 成本路由 / 语义缓存 / 置信降级 / 中文特化 / MLOps 骨架。

约定：
  - ``router = APIRouter(prefix="", tags=["quality"])``，路径写全；app 侧以
    ``prefix="/api/v1"`` include，因此实际路径为 ``/api/v1/eval/judge`` 等。
  - 全部端点登录（``Depends(get_caller)``）；无 token / 验签失败由 deps 抛 401。
  - 响应统一 ``{"code":0,"data":{...}}``；参数错误用 ``HTTPException``。
  - 业务实现都在 services 层，本文件只做参数校验 + 组装。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import degrade, judge, model_router, quality, semantic_cache
from app.services._m27_store import audit_event

router = APIRouter(prefix="", tags=["quality"])


def _require_keys(body: dict, *keys: str) -> None:
    missing = [k for k in keys if k not in (body or {})]
    if missing:
        raise HTTPException(status_code=422, detail=f"缺少必填字段：{', '.join(missing)}")


# ── 评测质量（第28章）──────────────────────────────────────────────────────
@router.post("/eval/judge")
async def eval_judge(body: dict, caller: CallerContext = Depends(get_caller)):
    """启发式评分（mode=heuristic，非 LLM-as-Judge）。"""
    _require_keys(body, "answer", "expected")
    data = quality.llm_judge(
        str(body.get("answer") or ""), str(body.get("expected") or ""),
        str(body.get("evidence") or ""),
    )
    return {"code": 0, "data": data}


@router.post("/eval/judge-one")
async def eval_judge_one(body: dict, caller: CallerContext = Depends(get_caller)):
    """LLM-as-Judge 单条判分（真模型；无真模型时诚实返回 llm_real=false）。

    入参：query（问题）、reference（标准依据）、answer（待评审答案）、可选 model。
    出参：correctness/groundedness/completeness/verdict/reason + llm_real。
    """
    _require_keys(body, "query", "reference", "answer")
    sc = judge.judge_one(
        str(body.get("query") or ""), str(body.get("reference") or ""),
        str(body.get("answer") or ""), model_id=body.get("model") or None,
    )
    return {"code": 0, "data": {
        "query": body.get("query"), "reference": body.get("reference"),
        "answer": body.get("answer"), "model": body.get("model") or None,
        "correctness": sc.correctness, "groundedness": sc.groundedness,
        "completeness": sc.completeness, "verdict": sc.verdict,
        "reason": sc.reason, "llm_real": sc.llm_real,
        "overall": sc.overall(),
    }}


@router.post("/eval/judge-run")
async def eval_judge_run(body: dict, caller: CallerContext = Depends(get_caller)):
    """LLM-as-Judge 整轮跑批（真模型；平台管理员）。

    入参：可选 golden（内联用例列表，否则用 services/qa_golden.jsonl）、可选 model。
    出参：按部门聚合 + 覆盖度 + 门禁结论（与 judge.run_judge 同构）。
    """
    if caller.role != "platform_admin":
        return {"code": 403, "msg": "forbidden: 仅平台管理员可跑判分"}
    golden = body.get("golden") if isinstance(body.get("golden"), list) else None
    try:
        report = judge.run_judge(golden, model_id=body.get("model") or None)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"code": 0, "data": report}


@router.post("/eval/judge-regression")
async def eval_judge_regression(body: dict, caller: CallerContext = Depends(get_caller)):
    """LLM-as-Judge 回归门禁（真模型；平台管理员）。

    跑一轮判分并与已存基线比对：平均分跌幅超阈值标 regression；可选 save_baseline 把本次存为基线。
    入参：可选 golden / model / save_baseline(bool)。
    """
    if caller.role != "platform_admin":
        return {"code": 403, "msg": "forbidden: 仅平台管理员可跑回归门禁"}
    golden = body.get("golden") if isinstance(body.get("golden"), list) else None
    try:
        report = judge.run_judge(golden, model_id=body.get("model") or None)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    reg = judge.regression_gate(report)
    if bool(body.get("save_baseline")):
        judge.save_baseline(report)
        reg["baseline_saved"] = True
    return {"code": 0, "data": {"report": report, "regression": reg}}


@router.get("/eval/judge-baseline")
async def eval_judge_baseline(caller: CallerContext = Depends(get_caller)):
    """读取已存的 LLM-as-Judge 回归基线（无则 null）。"""
    return {"code": 0, "data": judge.load_baseline()}


@router.post("/eval/judge-save-baseline")
async def eval_judge_save_baseline(body: dict, caller: CallerContext = Depends(get_caller)):
    """将一次判分报告存为回归基线（平台管理员）。入参：report（judge.run_judge 的结构化结果）。"""
    if caller.role != "platform_admin":
        return {"code": 403, "msg": "forbidden: 仅平台管理员可保存基线"}
    rep = body.get("report")
    if not isinstance(rep, dict):
        raise HTTPException(status_code=422, detail="report 必须是 judge.run_judge 的结果对象")
    judge.save_baseline(rep)
    return {"code": 0, "data": {"saved": True, "avg_overall": rep.get("avg_overall")}}


@router.post("/eval/prompt-regression")
async def eval_prompt_regression(body: dict, caller: CallerContext = Depends(get_caller)):
    """提示词回归门禁：候选均分不低于基线才 pass。"""
    _require_keys(body, "baseline", "candidate", "cases")
    if not isinstance(body.get("cases"), list):
        raise HTTPException(status_code=422, detail="cases 必须是列表")
    data = quality.prompt_regression(
        str(body.get("baseline") or ""), str(body.get("candidate") or ""),
        body.get("cases") or [],
    )
    return {"code": 0, "data": data}


@router.get("/eval/zh-bench")
async def eval_zh_bench(caller: CallerContext = Depends(get_caller)):
    """内置 6 条中文基准（内部小集，不等同 C-Eval/CMMLU）。"""
    return {"code": 0, "data": quality.zh_bench()}


# ── 中文特化（第42章）──────────────────────────────────────────────────────
@router.post("/zh/segment")
async def zh_segment(body: dict, caller: CallerContext = Depends(get_caller)):
    """中文语义边界切分（保留标点，长句切到 <=60 字）。"""
    _require_keys(body, "text")
    text = str(body.get("text") or "")
    segs = quality.chinese_segment(text)
    return {"code": 0, "data": {"text": text, "segments": segs, "count": len(segs)}}


# ── 成本路由（第34章）──────────────────────────────────────────────────────
@router.get("/models/tiers")
async def models_tiers(caller: CallerContext = Depends(get_caller)):
    """两档模型名、单价与当前开关。"""
    return {"code": 0, "data": model_router.tiers()}


@router.post("/routing/preview")
async def routing_preview(body: dict, caller: CallerContext = Depends(get_caller)):
    """按输入特征预览路由档位（不真的改调用链）。"""
    _require_keys(body, "input")
    agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
    data = model_router.route(str(body.get("input") or ""), agent)
    return {"code": 0, "data": data}


# ── 语义缓存（第38章）──────────────────────────────────────────────────────
@router.get("/ops/cache/stats")
async def ops_cache_stats(caller: CallerContext = Depends(get_caller)):
    """缓存统计（similarity=char_3gram_jaccard，未接 embedding）。"""
    return {"code": 0, "data": semantic_cache.stats()}


@router.post("/ops/cache/clear")
async def ops_cache_clear(caller: CallerContext = Depends(get_caller)):
    """清空缓存并重置命中率计数。"""
    semantic_cache.clear()
    audit_event("ops.cache.clear", caller, target="semantic_cache", result="ok")
    return {"code": 0, "data": {"cleared": True, "stats": semantic_cache.stats()}}


# ── 置信降级 / 转人工（第40章）────────────────────────────────────────────
@router.post("/handoff")
async def handoff_create(body: dict, caller: CallerContext = Depends(get_caller)):
    """低置信转人工工单（status=open，带完整上下文与置信理由）。"""
    _require_keys(body, "run_id", "agent_id", "reason", "context")
    context = body.get("context")
    if not isinstance(context, dict):
        raise HTTPException(status_code=422, detail="context 必须是对象")
    data = degrade.handoff(
        str(body.get("run_id") or ""), str(body.get("agent_id") or ""),
        str(body.get("reason") or ""), context, caller,
    )
    return {"code": 0, "data": data}


@router.get("/handoff")
async def handoff_list(status: str | None = Query(default=None),
                       caller: CallerContext = Depends(get_caller)):
    """列工单；可按 status 过滤，非管理员只看自己创建的。"""
    items = degrade.list_handoffs(caller, status)
    return {"code": 0, "data": {"items": items, "count": len(items)}}


@router.post("/handoff/{hid}/resolve")
async def handoff_resolve(hid: str, body: dict,
                          caller: CallerContext = Depends(get_caller)):
    """结单：status=resolved。工单不存在返回 404。"""
    _require_keys(body, "resolution")
    try:
        data = degrade.resolve(hid, str(body.get("resolution") or ""), caller)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"code": 0, "data": data}
