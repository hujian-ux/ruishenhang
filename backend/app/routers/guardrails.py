"""guardrails 路由：M27 AI 行为安全与工具层安全闭环（第 27/39/44 章）。

全部端点要求登录（Depends(get_caller)），响应统一 {"code":0,"data":{...}}。
业务主链路（invoke）后续直接 import services.guardrails 的函数，不经此路由；
本路由用于运维、联调与独立验证。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import guardrails as svc

router = APIRouter(prefix="/guardrails", tags=["guardrails"])


class ScanInputRequest(BaseModel):
    text: str = ""


class ScanOutputRequest(BaseModel):
    answer: str = ""
    evidence: str = ""
    hits: list | None = None


class GroundingRequest(BaseModel):
    answer: str = ""
    evidence: str = ""


class ToolCheckRequest(BaseModel):
    tool: str = ""
    args: dict = Field(default_factory=dict)


@router.post("/scan-input", summary="输入提示注入 / 越狱扫描")
async def scan_input(req: ScanInputRequest, caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.scan_input(req.text)}


@router.post("/scan-output", summary="输出脱敏 / 泄密 / 引用覆盖评估")
async def scan_output(req: ScanOutputRequest, caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.scan_output(
        req.answer, caller=caller, hits=req.hits, evidence=req.evidence,
    )}


@router.post("/grounding", summary="关键数字与证据的字面比对")
async def grounding(req: GroundingRequest, caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.grounding_check(req.answer, req.evidence)}


@router.post("/tool-check", summary="工具调用前置安全校验")
async def tool_check(req: ToolCheckRequest, caller: CallerContext = Depends(get_caller)):
    if not req.tool:
        raise HTTPException(status_code=400, detail="tool 不能为空")
    return {"code": 0, "data": svc.guard_tool_call(req.tool, req.args, caller)}


@router.get("/policy", summary="回显当前策略开关与规则数量（不含密钥）")
async def policy(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": svc.policy()}
