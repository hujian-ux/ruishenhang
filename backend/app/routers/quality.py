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
from app.services import degrade, mlops, model_router, quality, semantic_cache
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


# ── MLOps 骨架（第43章）───────────────────────────────────────────────────
@router.post("/mlops/register")
async def mlops_register(body: dict, caller: CallerContext = Depends(get_caller)):
    """登记模型元数据（骨架，仅内存）。"""
    _require_keys(body, "name", "version", "metrics")
    if not isinstance(body.get("metrics"), dict):
        raise HTTPException(status_code=422, detail="metrics 必须是对象")
    data = mlops.register_model(
        str(body.get("name") or ""), str(body.get("version") or ""),
        body.get("metrics") or {}, str(body.get("fingerprint") or ""),
    )
    return {"code": 0, "data": data}


@router.get("/mlops/models")
async def mlops_models(caller: CallerContext = Depends(get_caller)):
    """列出已登记模型。"""
    items = mlops.list_models()
    return {"code": 0, "data": {"items": items, "count": len(items)}}


@router.post("/mlops/drift")
async def mlops_drift(body: dict, caller: CallerContext = Depends(get_caller)):
    """简易 PSI + 均值漂移（骨架实现，未接训练流水线）。"""
    _require_keys(body, "baseline", "observed")
    if not isinstance(body.get("baseline"), list) or not isinstance(body.get("observed"), list):
        raise HTTPException(status_code=422, detail="baseline / observed 必须是列表")
    data = mlops.drift_check(body.get("baseline") or [], body.get("observed") or [])
    return {"code": 0, "data": data}


@router.get("/mlops/pipeline")
async def mlops_pipeline(caller: CallerContext = Depends(get_caller)):
    """流水线状态：四项 done=False + 落地条件，明确未训练。"""
    return {"code": 0, "data": mlops.pipeline_status()}
