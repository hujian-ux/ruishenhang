"""selection 路由：模型选型决策框架服务化（第35章）。

与前端「选型向导」、原型导出 JSON 同源（app.selection.framework.recommend）。
业务侧调用本端点获得 adapter_type 与落地章节，再用于 /agents 注册。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import SelectionRequest
from app.selection import framework

router = APIRouter(prefix="/selection", tags=["selection"])


@router.post("/recommend", summary="模型选型决策（第35章）")
async def recommend(req: SelectionRequest, caller: CallerContext = Depends(get_caller)):
    if caller.role not in ("employee", "dept_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="forbidden")
    return {"code": 0, "data": framework.recommend(req.input_type, req.deterministic, req.need)}
