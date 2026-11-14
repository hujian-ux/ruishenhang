"""P1-9 · 子代理 / 专家执行路由（#304）。

管理后台「专家执行台」的数据接口：可见专家目录 / 单次委派 / 批量并发委派 /
执行台账与看板。写操作仅为「触发一次无状态推理 + 记台账」，不改动任何技能包字段。

路由顺序注意：所有静态段（/stats /runs /dispatch /dispatch-batch）都必须注册在
路径参数段之前；明细特意用两段 `/runs/{run_id}` 而非单段 `/{run_id}`，从根上
避免 "stats" 被当 id 吞掉（同 P1-8 /skills 的处理）。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import subagents

router = APIRouter(prefix="/experts", tags=["subagents"])


class DispatchRequest(BaseModel):
    agent_id: str = Field(..., description="专家技能包 agent_id")
    task: str = Field(..., description="委派的子任务")
    context: str = Field("", description="上游主任务背景 / 前序结论")
    parent_agent_id: str = Field("", description="发起委派的主智能体 agent_id（台账用）")
    depth: int = Field(0, ge=0, description="委派深度，超限由服务端拒绝")


class BatchTask(BaseModel):
    agent_id: str
    task: str
    context: str = ""


class DispatchBatchRequest(BaseModel):
    tasks: List[BatchTask]
    context: str = Field("", description="所有子任务共享的上游上下文")
    parent_agent_id: str = ""
    depth: int = Field(0, ge=0)


# ── 静态段（全部先于任何路径参数）────────────────────────────────────────────
@router.get("/stats")
async def expert_stats(caller: CallerContext = Depends(get_caller)):
    """执行台账看板：总量 / 成功与降级 / 按专家 TOP / 累计用量。"""
    return {"code": 0, "data": subagents.stats(caller)}


@router.get("/runs")
async def expert_runs(
    agent_id: str = Query("", description="按专家过滤"),
    status: str = Query("", description="ok|unavailable|denied|error"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: CallerContext = Depends(get_caller),
):
    """执行台账（RBAC 隔离，最新在前）。"""
    d = subagents.list_runs(caller, agent_id=agent_id, status=status,
                            limit=limit, offset=offset)
    return {"code": 0, "data": d}


@router.post("/dispatch-batch")
async def expert_dispatch_batch(req: DispatchBatchRequest,
                                caller: CallerContext = Depends(get_caller)):
    """并发委派多个专家（最多 SUBAGENT_MAX_BATCH 个），单条失败不影响其它。"""
    tasks: List[Dict[str, Any]] = [
        {"agent_id": t.agent_id, "task": t.task, "context": t.context} for t in req.tasks
    ]
    d = subagents.dispatch_batch(caller, tasks, context=req.context,
                                 parent_agent_id=req.parent_agent_id, depth=req.depth)
    return {"code": 0, "data": d}


@router.post("/dispatch")
async def expert_dispatch(req: DispatchRequest,
                          caller: CallerContext = Depends(get_caller)):
    """把单个子任务委派给指定专家独立执行。"""
    d = subagents.dispatch(
        req.agent_id, req.task, caller, context=req.context,
        parent_agent_id=req.parent_agent_id, depth=req.depth, source="api",
    )
    return {"code": 0, "data": d}


@router.get("")
async def expert_index(
    industry: str = Query("", description="按行业过滤（category_cn）"),
    role: str = Query("", description="岗位 id 或中文名"),
    q: str = Query("", description="搜索名称/描述/子职能"),
    kind: str = Query("", description="business|public|空(全部)"),
    limit: int = Query(200, ge=1, le=500),
    caller: CallerContext = Depends(get_caller),
):
    """可见专家目录（业务包 + 公共库，排除内置助手）。"""
    d = subagents.list_experts(caller, industry=industry, role=role, q=q,
                               kind=kind, limit=limit)
    return {"code": 0, "data": d}


# ── 路径参数段（放最后）────────────────────────────────────────────────────
@router.get("/runs/{run_id}")
async def expert_run_detail(run_id: str, caller: CallerContext = Depends(get_caller)):
    """单条执行明细（含完整结论）。不可见 / 不存在统一 404，不区分避免信息泄露。"""
    rec = subagents.get_run(caller, run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"执行记录不存在或无权限：{run_id}")
    return {"code": 0, "data": rec}
