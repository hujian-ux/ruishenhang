"""models 路由：可插拔模型注册表（第25章）。"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import (
    ModelListResponse, ModelRegisterRequest, ModelRegisterResponse,
)
from app.services import model_gateway

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelListResponse)
async def list_models(status: str = Query("active"), caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"models": model_gateway.list_models(status)}}


@router.post("", response_model=ModelRegisterResponse)
async def register_model(req: ModelRegisterRequest, caller: CallerContext = Depends(get_caller)):
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可注册模型")
    return {"code": 0, "data": model_gateway.register_model(req.model_dump())}
