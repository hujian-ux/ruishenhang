"""agents 路由：注册智能体（第35章选型结论落库，红线：缺选型不可发布）。"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import AgentRegisterRequest, AgentRegisterResponse
from app.selection import framework

router = APIRouter(prefix="/agents", tags=["agent"])

VALID_ADAPTERS = set(framework.ADAPTER_META.keys())
VALID_SCOPES = {"personal", "owner", "department", "global"}
_AGENTS: dict[str, dict] = {}


@router.post("", response_model=AgentRegisterResponse)
async def register_agent(req: AgentRegisterRequest, caller: CallerContext = Depends(get_caller)):
    # 红线：adapter_type 来自选型向导，缺失即拒绝发布
    if not req.adapter_type:
        raise HTTPException(status_code=400, detail="缺选型不可发布：adapter_type 必填（第35章）")
    if req.adapter_type not in VALID_ADAPTERS:
        raise HTTPException(
            status_code=400,
            detail=f"非法 adapter_type：{req.adapter_type}，应为 {sorted(VALID_ADAPTERS)}",
        )
    if req.knowledge_scope not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail=f"非法 knowledge_scope：{req.knowledge_scope}")
    # 服务端强制回填 registered_by，前端不可伪造
    slug = re.sub(r"[^a-z0-9]", "", req.name.lower())
    agent_id = req.agent_id or ("ag_" + (slug[:16] or uuid.uuid4().hex[:8]))
    sel = framework.ADAPTER_META.get(req.adapter_type, {})
    _AGENTS[agent_id] = {
        "agent_id": agent_id, "name": req.name, "adapter_type": req.adapter_type,
        "knowledge_scope": req.knowledge_scope, "model_id": req.model_id,
        "registered_by": caller.userId, "status": "pending_review",
    }
    return {
        "code": 0,
        "data": {
            "agent_id": agent_id,
            "status": "pending_review",  # 进入待发布评审（第35章红线）
            "adapter_type": req.adapter_type,
            "registered_by": caller.userId,
            "selection": {"recommended": req.adapter_type, "landing_chapters": sel.get("chapters", "")},
        },
    }


@router.get("")
async def list_agents(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"agents": list(_AGENTS.values()), "count": len(_AGENTS)}}
