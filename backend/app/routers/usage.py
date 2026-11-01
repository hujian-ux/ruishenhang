"""usage 路由：Token 用量与计费（第25章，按维度聚合，受 scope 约束）。"""
from fastapi import APIRouter, Depends, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import UsageSummary

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageSummary)
async def query_usage(
    dimension: str = Query("dept"),
    start: str = Query(None),
    end: str = Query(None),
    caller: CallerContext = Depends(get_caller),
):
    # 骨架：返回演示聚合。cost 属敏感指标，跨部门汇总仅平台管理员可见（第25章）。
    rows = [
        {"key": caller.deptId, "prompt_tokens": 1820000, "completion_tokens": 940000,
         "cost": 312.5, "quota": 1000, "quota_used_pct": 31.3},
    ]
    return {"code": 0, "data": {"dimension": dimension, "period": "2026-11", "rows": rows}}
