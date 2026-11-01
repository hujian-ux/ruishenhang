"""kb 路由：知识写入（个人/部门/集团），服务端强制回填权限。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import KbDocumentRequest, KbDocumentResponse

router = APIRouter(prefix="/kb", tags=["kb"])


@router.post("/documents", response_model=KbDocumentResponse)
async def create_document(req: KbDocumentRequest, caller: CallerContext = Depends(get_caller)):
    # 越权写拦截：department 时 dept_id 必须等于 caller.deptId
    if req.scope == "department" and req.dept_id and req.dept_id != caller.deptId:
        raise HTTPException(status_code=403, detail="forbidden: dept_id 越权")
    if req.scope == "global" and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可写 global")
    # 服务端强制回填 owner / dept / tenant（前端传入仅展示）
    doc_id = "doc_" + uuid.uuid4().hex[:8]
    return {
        "code": 0,
        "data": {
            "doc_id": doc_id,
            "scope": req.scope,
            "owner_id": caller.userId,
            "dept_id": caller.deptId,
            "indexed": False,  # M2 接入向量化后变 true
        },
    }
