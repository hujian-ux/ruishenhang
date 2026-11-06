"""M27 · 运维路由（prefix=/ops）：指标 / 健康 / 告警 / 就绪度 / 审计导出 / 自助删数据。

全部端点需登录（Depends(get_caller)）；成功响应统一 {"code":0,"data":{...}}。
/ops/metrics 例外：text/plain 的 Prometheus 文本。
/ops/audit-export 与 /ops/me/data 的结果按 caller 收敛（复用既有 RBAC / userId）。
"""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import observability

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(caller: CallerContext = Depends(get_caller)):
    """Prometheus exposition（text/plain）；仍需登录。"""
    return PlainTextResponse(content=observability.prometheus_text(), media_type="text/plain")


@router.get("/health")
def health(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": observability.health(caller)}


@router.get("/alerts")
def get_alerts(caller: CallerContext = Depends(get_caller)):
    items = observability.alerts()
    h = observability.health(caller)
    return {
        "code": 0,
        "data": {
            "alerts": items,
            "count": len(items),
            # 样本不足时 alerts 为空，说明落在这里（reason），不误报也不失语
            "insufficient_data": h["insufficient_data"],
        },
    }


@router.get("/readiness")
def readiness(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": observability.readiness()}


@router.get("/audit-export")
def audit_export(caller: CallerContext = Depends(get_caller)):
    # 只读取，范围由 audit.get_logs(caller) 的既有 RBAC 收敛
    return {"code": 0, "data": observability.audit_export(caller)}


@router.delete("/me/data")
def delete_my_data(caller: CallerContext = Depends(get_caller)):
    # 只删 caller.userId 名下数据；其它 store 的数据列 manual_review，不谎称全删
    return {"code": 0, "data": observability.delete_my_data(caller)}
