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


@router.post("/purge")
async def purge_audit(caller: CallerContext = Depends(get_caller),
                      retention_days: int = 0):
    """按留存策略清理过期审计（法定保留动作永不过期）。仅平台管理员。"""
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可执行留存清理")
    deleted = audit_svc.purge_expired(retention_days or None)
    return {"code": 0, "data": {"deleted": deleted,
                                "retention_days": retention_days or None,
                                "legal_hold": audit_svc._legal_hold()}}


@router.post("/export")
async def export_audit(caller: CallerContext = Depends(get_caller),
                       encrypt: bool = True):
    """只读导出 caller 可见的审计记录（RBAC 收敛，不改数据）。仅平台管理员。

    配置了 AUDIT_EXPORT_KEY 时加密导出（.enc），否则明文 CSV（并告警）。
    """
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可导出审计")
    result = audit_svc.export_records(caller, encrypt=encrypt if encrypt else None)
    return {"code": 0, "data": result}
