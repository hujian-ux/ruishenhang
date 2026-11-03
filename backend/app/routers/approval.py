"""approval 路由：M5 审批流真实化（第35章红线：评审通过才发布）。

- POST /approval/{approval_id}/decide：决策（仅 platform_admin / 本部门 dept_admin）
- GET  /approval/pending：待审列表（管理员）
决策通过 → 智能体 pending_review→published（可被 invoke）；驳回 → rejected。
"""
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam

from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import ApprovalDecisionRequest, ApprovalDecisionResponse
from app.services import agent_store, approval_store, audit

router = APIRouter(prefix="/approval", tags=["approval"])


def _require_admin(caller: CallerContext) -> None:
    if caller.role not in ("platform_admin", "dept_admin"):
        raise HTTPException(status_code=403, detail="forbidden: 无审批权限")


@router.post("/{approval_id}/decide", response_model=ApprovalDecisionResponse)
async def decide(
    approval_id: str = PathParam(...),
    req: ApprovalDecisionRequest = None,
    caller: CallerContext = Depends(get_caller),
    trace_id: str = Depends(get_trace_id),
):
    _require_admin(caller)
    if req is None or req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 必须为 approve|reject")
    try:
        ticket = approval_store.decide(
            approval_id, req.decision, caller.userId, caller.role, caller.deptId, req.comment,
        )
    except approval_store.TicketNotFound:
        audit.record("approval", caller, target=approval_id, result="error:not_found", trace_id=trace_id)
        raise HTTPException(status_code=404, detail=f"审批单不存在：{approval_id}")
    except approval_store.DuplicateDecision:
        audit.record("approval", caller, target=approval_id, result="error:duplicate", trace_id=trace_id)
        raise HTTPException(status_code=409, detail="该审批单已决策（终态不可改）")
    except approval_store.ForbiddenDecider:
        audit.record("approval", caller, target=approval_id, result="error:forbidden", trace_id=trace_id)
        raise HTTPException(status_code=403, detail="dept_admin 仅可审批本部门发起的单据")
    # 智能体状态同步迁移（第35章红线：评审通过才发布）
    # M15：批次单一次决策整批迁移，避免 115 个技能包逐个点。
    target_ids = ticket.get("agent_ids") or [ticket["agent_id"]]
    new_status = "published" if ticket["status"] == "approved" else "rejected"
    migrated = []
    for aid in target_ids:
        if agent_store.set_status(aid, new_status):
            migrated.append(aid)
    agent = agent_store.get(ticket["agent_id"])
    audit.record("approval", caller, target=approval_id, result=ticket["status"],
                 meta={"agent_id": ticket["agent_id"], "agent_ids": target_ids,
                       "batch": bool(ticket.get("batch")), "decision": req.decision,
                       "migrated": len(migrated)}, trace_id=trace_id)
    return {
        "code": 0,
        "data": {
            "approval_id": approval_id,
            "status": ticket["status"],
            "agent_id": ticket["agent_id"],
            "agent_status": agent["status"] if agent else None,
            "batch": bool(ticket.get("batch")),
            "migrated_count": len(migrated),
            "decided_by": caller.userId,
            "comment": ticket["comment"],
        },
    }


@router.get("/pending")
async def pending(caller: CallerContext = Depends(get_caller)):
    """待审列表：platform_admin 全量，dept_admin 本部门。"""
    _require_admin(caller)
    dept = caller.deptId if caller.role == "dept_admin" else None
    rows = approval_store.pending_list(dept)
    return {"code": 0, "data": {"count": len(rows), "tickets": rows}}
