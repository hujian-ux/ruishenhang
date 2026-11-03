"""audit 路由：M13 审计日志查询（RBAC 收敛，与计量解耦）。

- GET /audit/logs：按身份收敛（platform_admin 全量 / dept_admin 本部门 / employee 本人）
- 与 usage（计量）是两套独立数据：usage 用于对账计费，audit 用于合规追溯
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import audit as audit_svc

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs")
async def get_audit_logs(
    caller: CallerContext = Depends(get_caller),
    action: Optional[str] = Query(default=None, description="过滤动作：retrieve/generate/download/approval"),
    limit: int = Query(default=100, ge=1, le=1000),
):
    rows = audit_svc.get_logs(caller, action=action, limit=limit)
    return {
        "code": 0,
        "data": {
            "count": len(rows),
            "backend": "pg" if audit_svc._connect_pg() is not None else "sqlite",
            "logs": rows,
        },
    }


@router.get("/health")
async def audit_health():
    return {"code": 0, "data": {"available": audit_svc.available()}}
