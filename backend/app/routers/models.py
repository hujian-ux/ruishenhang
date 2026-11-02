"""models 路由：可插拔模型注册表（第25章）。

M7 管理页支撑：列表带网关健康状态（LiteLLM 是否可达 + 当前默认模型），
并支持平台管理员启停模型（下线某个模型不需改业务代码）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import (
    ModelListResponse, ModelRegisterRequest, ModelRegisterResponse,
)
from app.services import model_gateway

router = APIRouter(prefix="/models", tags=["models"])


def _gateway_status() -> dict:
    return {
        "available": model_gateway.llm_available(),
        "base_url": settings.LITELLM_BASE_URL,
        "default_model": settings.LLM_DEFAULT_MODEL,
    }


@router.get("", response_model=ModelListResponse)
async def list_models(status: str = Query("active"), caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"models": model_gateway.list_models(status),
                                "gateway": _gateway_status()}}


@router.get("/gateway")
async def gateway_health(caller: CallerContext = Depends(get_caller)):
    """模型网关探活（页面顶部状态灯）；force 重新探测不读缓存。"""
    return {"code": 0, "data": {**_gateway_status(), "probe": "live"}}


@router.patch("/{model_id}")
async def patch_model(model_id: str, body: dict, caller: CallerContext = Depends(get_caller)):
    """启停模型（仅平台管理员）。body: {"status": "active"|"inactive"}"""
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可启停模型")
    status = (body or {}).get("status")
    if status not in ("active", "inactive"):
        raise HTTPException(status_code=422, detail="status 必须为 active 或 inactive")
    try:
        m = model_gateway.set_status(model_id, status)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"模型不存在：{model_id}")
    return {"code": 0, "data": {"model": m, "changed_by": caller.userId}}


@router.post("", response_model=ModelRegisterResponse)
async def register_model(req: ModelRegisterRequest, caller: CallerContext = Depends(get_caller)):
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可注册模型")
    return {"code": 0, "data": model_gateway.register_model(req.model_dump())}
