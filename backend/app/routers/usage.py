"""usage 路由：Token 用量与计费（第25章计量 / 第30章成本），按维度聚合且受 scope 约束。

M7 真实化：数据来自每次真实调用的落账（usage_store），不再返回演示常量。
维度：dept / agent / model / user / day；权限：员工→本人、部门管理员→本部门、
平台管理员→全量（跨部门金额汇总只有平台管理员看得到完整列表）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import UsageSummary
from app.services import usage_store

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageSummary)
@router.get("/summary", response_model=UsageSummary)
async def query_usage(
    dimension: str = Query("dept", description="dept|agent|model|user|day"),
    days: int = Query(30, ge=1, le=365, description="回溯窗口（天）"),
    caller: CallerContext = Depends(get_caller),
):
    if dimension not in usage_store.DIMENSIONS:
        raise HTTPException(status_code=422,
                            detail=f"不支持的维度：{dimension}（可选 {','.join(usage_store.DIMENSIONS)}）")
    return {"code": 0, "data": usage_store.summary(dimension, caller, days)}


@router.get("/records")
async def list_records(limit: int = Query(50, ge=1, le=500),
                       caller: CallerContext = Depends(get_caller)):
    """最近调用明细（每行一单：时间/智能体/模型/token/成本/真伪/延迟）。"""
    rows = usage_store.list_records(caller, limit=limit)
    return {"code": 0, "data": {"records": rows, "count": len(rows),
                                "cost_currency": "CNY"}}


@router.get("/quota")
async def quota_status(caller: CallerContext = Depends(get_caller)):
    """本部门配额水位（滚动窗口）；超限时调用会被 429 拦截。"""
    return {"code": 0, "data": usage_store.quota_status(caller)}


@router.get("/trend")
async def usage_trend(days: int = Query(14, ge=1, le=365, description="回溯天数（自然日，连续补齐）"),
                      caller: CallerContext = Depends(get_caller)):
    """成本曲线（M8 试点）：逐日用量/成本 + 月累计 + 月末外推 + 预算水位。

    面试/评审常问「这套东西一个月烧多少钱」——这里给的是真落账数据外推，
    不是拍脑袋的单价表。
    """
    return {"code": 0, "data": usage_store.trend(caller, days)}
