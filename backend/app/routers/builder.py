"""M27 · 自助建部门 / L2 员工零代码建智能体 / L3 节点式工作流编排 统一路由。

全部端点登录可用（Depends(get_caller)）；权限判定下沉到 service 层
（approve / publish 仅 platform_admin）。响应统一 {"code":0,"data":{...}}，
错误由 service 抛 HTTPException。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
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
    edges: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowUpdateRequest(BaseModel):
    name: str
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    input: str = ""
    agent_id: str | None = None


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
    out = workflows.create_workflow(req.name, req.nodes, caller, edges=req.edges)
    return {"code": 0, "data": out}


@router.get("/workflows/{wid}")
async def get_workflow(wid: str, caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": workflows.get_workflow(wid, caller)}


@router.put("/workflows/{wid}")
async def put_workflow(wid: str, req: WorkflowUpdateRequest,
                       caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": workflows.update_workflow(wid, req.name, req.nodes, req.edges, caller)}


@router.get("/workflows")
async def get_workflows(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"workflows": workflows.list_workflows(caller)}}


@router.post("/workflows/{wid}/run")
async def post_workflow_run(wid: str, req: WorkflowRunRequest,
                            caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": workflows.run_workflow(wid, req.input, caller)}


@router.post("/workflows/{wid}/execute")
async def post_workflow_execute(wid: str, req: WorkflowRunRequest,
                               caller: CallerContext = Depends(get_caller)):
    """工作流真执行（P1-6）：llm 节点真实调模型，前序输出贯穿后续节点；
    可选 agent_id 绑定智能体人设。返回完整执行结果（dry_run=False）。"""
    return {"code": 0, "data": workflows.run_workflow_real(
        wid, req.input, caller, agent_id=req.agent_id)}


@router.post("/workflows/{wid}/execute-stream")
async def post_workflow_execute_stream(wid: str, req: WorkflowRunRequest,
                                      caller: CallerContext = Depends(get_caller)):
    """工作流真执行 SSE：逐步下发 meta/node_start/node_result/done 事件。"""
    async def _gen():
        async for ev in workflows.arun_workflow_stream(
                wid, req.input, caller, agent_id=req.agent_id):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    return StreamingResponse(_gen(), media_type="text/event-stream")
