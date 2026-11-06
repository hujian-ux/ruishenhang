"""M27 路由：反馈闭环 / 演化建议台 / 运营与质量度量（蓝图第 20/28/30/41 章）。

prefix 为空、路径写全（挂载方统一加 /api/v1）。全部端点需登录（Depends(get_caller)）；
decide / rollback 仅 platform_admin、dept_admin 可执行，非管理员返回 403。
响应统一 {"code": 0, "data": {...}}；业务错误用 HTTPException。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import feedback as svc
from app.services._m27_store import is_admin

router = APIRouter(prefix="", tags=["feedback"])


def _require_admin(caller: CallerContext) -> None:
    if not is_admin(caller):
        raise HTTPException(status_code=403,
                            detail="forbidden: 仅平台/部门管理员可执行该操作")


def _guard(fn, *args, **kwargs):
    """把业务层异常翻译成 HTTP：权限 403 / 不存在 404 / 参数 400。"""
    try:
        return fn(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- 反馈闭环 --------------------------------------------------------------
@router.post("/feedback")
async def post_feedback(body: dict, caller: CallerContext = Depends(get_caller)):
    """提交赞/踩；负反馈或纠错自动进入知识缺口候选。"""
    b = body or {}
    row = _guard(
        svc.add_feedback,
        run_id=b.get("run_id", ""), session_id=b.get("session_id", ""),
        agent_id=b.get("agent_id", ""), caller=caller,
        vote=b.get("vote", ""), correction=b.get("correction", ""),
        reason=b.get("reason", ""), score=b.get("score"),
    )
    return {"code": 0, "data": row}


@router.get("/feedback/stats")
async def get_feedback_stats(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.feedback_stats(caller)}


# ---- 演化建议台 ------------------------------------------------------------
@router.get("/evolution/gaps")
async def get_gaps(caller: CallerContext = Depends(get_caller)):
    gaps = svc.detect_gaps()
    return {"code": 0, "data": {"gaps": gaps, "count": len(gaps)}}


@router.get("/evolution/proposals")
async def get_proposals(status: str | None = Query(default=None),
                        caller: CallerContext = Depends(get_caller)):
    rows = svc.list_proposals(caller, status=status)
    return {"code": 0, "data": {"proposals": rows, "count": len(rows)}}


@router.post("/evolution/proposals")
async def post_proposal(body: dict, caller: CallerContext = Depends(get_caller)):
    """新建演化建议，status 恒为 pending，绝不自动生效。"""
    b = body or {}
    row = _guard(
        svc.create_proposal,
        kind=b.get("kind", ""), title=b.get("title", ""),
        payload=b.get("payload") or {}, caller=caller,
        source=b.get("source", ""),
    )
    return {"code": 0, "data": row}


@router.post("/evolution/proposals/{pid}/decide")
async def decide_proposal(pid: str, body: dict,
                          caller: CallerContext = Depends(get_caller)):
    """审批（仅管理员）：approve → applied 并写版本快照；reject → rejected。"""
    _require_admin(caller)
    b = body or {}
    row = _guard(svc.decide_proposal, pid, b.get("decision", ""), caller)
    return {"code": 0, "data": row}


@router.get("/evolution/versions")
async def get_versions(caller: CallerContext = Depends(get_caller)):
    rows = svc.list_versions()
    return {"code": 0, "data": {"versions": rows, "count": len(rows)}}


@router.post("/evolution/rollback")
async def post_rollback(body: dict, caller: CallerContext = Depends(get_caller)):
    """回滚（仅管理员）：生成一条新的 pending 建议，不静默改库。"""
    _require_admin(caller)
    b = body or {}
    version_id = b.get("version_id", "")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id 必填")
    result = _guard(svc.rollback, version_id, caller)
    return {"code": 0, "data": result}


# ---- 运营与质量度量 --------------------------------------------------------
@router.get("/metrics/adoption")
async def get_adoption(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.adoption_metrics(caller)}


@router.get("/metrics/quality")
async def get_quality(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.quality_metrics(caller)}


@router.post("/metrics/quality")
async def post_quality(body: dict, caller: CallerContext = Depends(get_caller)):
    """供 invoke 埋点：body 即事件对象，也兼容 {"event": {...}} 包装。"""
    b = body or {}
    event = b.get("event") if isinstance(b.get("event"), dict) else b
    svc.record_quality(event)
    return {"code": 0, "data": {"recorded": True}}
