"""approval 路由：审批决策（入驻/共享/导出），仅管理员可审。"""
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import ApprovalDecisionRequest, ApprovalDecisionResponse

router = APIRouter(prefix="/approval", tags=["approval"])


@router.post("/{approval_id}/decide", response_model=ApprovalDecisionResponse)
async def decide(
    approval_id: str = PathParam(...),
    req: ApprovalDecisionRequest = None,
    caller: CallerContext = Depends(get_caller),
):
    if caller.role not in ("platform_admin", "dept_admin"):
        raise HTTPException(status_code=403, detail="forbidden: 无审批权限")
    return {
        "code": 0,
        "data": {
            "approval_id": approval_id,
            "status": "approved" if req and req.decision == "approve" else "rejected",
            "decided_by": caller.userId,
            "audit_id": "audit_skeleton",
        },
    }
