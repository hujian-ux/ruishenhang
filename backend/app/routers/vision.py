"""P1-10 · 多模态图像理解 HTTP 端点（#305）。

- GET  /vision/status    通道状态：是否启用、当前 provider/model、限制、是否已就绪
- POST /vision/analyze   按 file_id 解读图片，回结构化结果（成功才带 text）

前端先调 status 决定要不要放开图片上传：开关没开时上传接口仍是 415，
避免出现「传上去了却永远不会被解读」的假成功。两个端点都需要登录态，
文件可见性完全复用 `file_store.can_access`（越权与不存在统一 404）。
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.services import audit, vision

router = APIRouter(prefix="/vision", tags=["vision"])


class AnalyzeRequest(BaseModel):
    file_ids: List[str] = Field(default_factory=list, description="已上传图片的 file_id 列表")
    prompt: str = Field("", description="解读要求；留空用「客观描述可核实内容」默认提示词")


@router.get("/status")
async def vision_status(caller: CallerContext = Depends(get_caller)):
    """通道状态。不回任何密钥，只回是否配置（前端据此禁用/启用上传区）。"""
    return {"code": 0, "data": vision.status()}


@router.post("/analyze")
async def analyze_images(req: AnalyzeRequest, caller: CallerContext = Depends(get_caller),
                         trace_id: str = Depends(get_trace_id)):
    """解读图片。`ok=False` 时 `text` 一定为空 —— 前端不要展示伪结论。"""
    try:
        result = vision.analyze(req.file_ids, caller, prompt=req.prompt)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"视觉通道异常：{type(exc).__name__}: {exc}"[:300])
    audit.record(
        "vision_analyze", caller, target=",".join(req.file_ids)[:200],
        result="ok" if result.get("ok") else "unavailable",
        meta={"ok": bool(result.get("ok")), "reason": result.get("reason"),
              "provider": result.get("provider"), "model": result.get("model"),
              "images": len(result.get("images") or []),
              "errors": len(result.get("errors") or []),
              "real": bool(result.get("real"))},
        trace_id=trace_id,
    )
    return {"code": 0, "data": result}
