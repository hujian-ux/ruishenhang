"""M27 · 自助建部门 / L2 员工零代码建智能体 / L3 节点式工作流编排 统一路由。

全部端点登录可用（Depends(get_caller)）；权限判定下沉到 service 层
（approve / publish 仅 platform_admin）。响应统一 {"code":0,"data":{...}}，
错误由 service 抛 HTTPException。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import agent_builder, dept_onboarding, workflows

router = APIRouter(prefix="/builder", tags=["builder"])


class AgentBuildRequest(BaseModel):
    name: str
    goal: str = ""
    inputs_contract: str = ""
    outputs_contract: str = ""
    steps: List[str] = Field(default_factory=list)
    scope: str = "department"


class DepartmentCreateRequest(BaseModel):
    name: str
    parent: str = ""
    domain: str = ""
    description: str = ""
    template: str = "blank"


class WorkflowCreateRequest(BaseModel):
    name: str
    nodes: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    input: str = ""


@router.get("/templates")
async def get_templates(caller: CallerContext = Depends(get_caller)):
    """部门模板（6 类）+ L2 建智能体模板。"""
    return {"code": 0, "data": {
        "department_templates": dept_onboarding.templates(),
        "agent_templates": agent_builder.builder_templates(),
    }}


@router.post("/agents")
async def post_agent(req: AgentBuildRequest, caller: CallerContext = Depends(get_caller)):
    out = agent_builder.build_agent(
        req.name, req.goal, req.inputs_contract, req.outputs_contract,
        req.steps, caller, scope=req.scope)
    return {"code": 0, "data": out}


@router.get("/agents")
async def get_agents(status: Optional[str] = Query(default=None),
                     caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"agents": agent_builder.list_drafts(caller, status=status)}}


@router.post("/agents/{agent_id}/publish")
async def post_agent_publish(agent_id: str, caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": agent_builder.publish(agent_id, caller)}


@router.get("/departments/templates")
async def get_department_templates(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": dept_onboarding.templates()}


@router.post("/departments")
async def post_department(req: DepartmentCreateRequest,
                          caller: CallerContext = Depends(get_caller)):
    out = dept_onboarding.create_department(
        req.name, req.parent, req.domain, req.description, req.template, caller)
    return {"code": 0, "data": out}


@router.get("/departments")
async def get_departments(status: Optional[str] = Query(default=None),
                          caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"departments": dept_onboarding.list_departments(caller, status=status)}}


@router.post("/departments/{did}/approve")
async def post_department_approve(did: str, caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": dept_onboarding.approve(did, caller)}


@router.post("/workflows")
async def post_workflow(req: WorkflowCreateRequest,
                        caller: CallerContext = Depends(get_caller)):
    out = workflows.create_workflow(req.name, req.nodes, caller)
    return {"code": 0, "data": out}


@router.get("/workflows")
async def get_workflows(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"workflows": workflows.list_workflows(caller)}}


@router.post("/workflows/{wid}/run")
async def post_workflow_run(wid: str, req: WorkflowRunRequest,
                            caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": workflows.run_workflow(wid, req.input, caller)}
