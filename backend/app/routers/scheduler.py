"""P0-5 调度与可靠投递路由（prefix=/scheduler）。

所有端点需登录；tenant 隔离为红线（只能看/触发本租户任务）。
操作类端点额外校验归属（本人创建或平台/部门管理员）。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import outbox, scheduler_service

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class AutomationIn(BaseModel):
    name: str
    agent_id: str
    input_template: str = ""
    schedule_type: str                       # cron | interval | once
    schedule_expr: str = ""                  # 标准 cron / 秒数(可带 s/m/h) / ISO 时间
    enabled: bool = True
    webhook_url: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


def _owns(caller: CallerContext, auto: Dict[str, Any]) -> bool:
    if auto.get("tenant_id") != caller.tenantId:
        return False
    if caller.role in ("platform_admin", "dept_admin"):
        return True
    return auto.get("owner_id") == caller.userId


@router.post("/automations")
def create(body: AutomationIn, caller: CallerContext = Depends(get_caller)):
    try:
        aid = scheduler_service.create_automation(
            caller, body.name, body.agent_id, body.input_template,
            body.schedule_type, body.schedule_expr, body.enabled,
            body.webhook_url, body.payload)
    except ValueError as e:
        return {"code": 1, "message": str(e)}
    return {"code": 0, "data": {"automation_id": aid}}


@router.get("/automations")
def list_all(caller: CallerContext = Depends(get_caller)):
    rows = scheduler_service.list_automations(caller.tenantId)
    return {"code": 0, "data": {"automations": rows, "count": len(rows)}}


@router.post("/automations/{automation_id}/run")
def run(automation_id: str, caller: CallerContext = Depends(get_caller)):
    auto = scheduler_service.get(automation_id)
    if not auto:
        return {"code": 1, "message": "not_found"}
    if not _owns(caller, auto):
        return {"code": 1, "message": "forbidden"}
    res = scheduler_service.run_now(automation_id)
    return {"code": 0, "data": res}


@router.post("/automations/{automation_id}/pause")
def pause(automation_id: str, caller: CallerContext = Depends(get_caller)):
    auto = scheduler_service.get(automation_id)
    if not auto:
        return {"code": 1, "message": "not_found"}
    if not _owns(caller, auto):
        return {"code": 1, "message": "forbidden"}
    return {"code": 0, "data": {"ok": scheduler_service.pause(automation_id)}}


@router.post("/automations/{automation_id}/resume")
def resume(automation_id: str, caller: CallerContext = Depends(get_caller)):
    auto = scheduler_service.get(automation_id)
    if not auto:
        return {"code": 1, "message": "not_found"}
    if not _owns(caller, auto):
        return {"code": 1, "message": "forbidden"}
    return {"code": 0, "data": {"ok": scheduler_service.resume(automation_id)}}


@router.delete("/automations/{automation_id}")
def delete(automation_id: str, caller: CallerContext = Depends(get_caller)):
    auto = scheduler_service.get(automation_id)
    if not auto:
        return {"code": 1, "message": "not_found"}
    if not _owns(caller, auto):
        return {"code": 1, "message": "forbidden"}
    return {"code": 0, "data": {"ok": scheduler_service.delete(automation_id)}}


@router.get("/outbox")
def outbox_list(status: Optional[str] = None, caller: CallerContext = Depends(get_caller)):
    rows = outbox.list_outbox(caller.tenantId, status)
    return {"code": 0, "data": {"outbox": rows, "count": len(rows)}}


@router.post("/outbox/{outbox_id}/retry")
def outbox_retry(outbox_id: str, caller: CallerContext = Depends(get_caller)):
    # 仅能重投本租户条目
    rows = outbox.list_outbox(caller.tenantId)
    if not any(r["outbox_id"] == outbox_id for r in rows):
        return {"code": 1, "message": "forbidden_or_not_found"}
    return {"code": 0, "data": {"ok": outbox.retry(outbox_id)}}
