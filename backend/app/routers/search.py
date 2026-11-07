"""M26 · 联网搜索路由。

- POST /search/web    执行一次公网检索（带 RBAC 身份与审计）
- GET  /search/status 通道状态：provider、是否有 key、是否降级（不回显密钥）

与 /rag/retrieve 的分工：
    /rag/retrieve  查**企业内部授权文档**，结果受部门/租户过滤点约束；
    /search/web    查**公网公开信息**，结果只在当轮上下文使用，不入库。
两条通道在响应里分别用 citations 与 web_sources 回传，永不合并。
"""
from fastapi import APIRouter, Depends

from app.core.config import settings
from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import WebSearchRequest
from app.services import audit, web_search
from app.services import search_intel

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/web")
async def search_web(req: WebSearchRequest, caller: CallerContext = Depends(get_caller),
                     trace_id: str = Depends(get_trace_id)):
    # M26 P1：opt-in 检索词拆解。默认关，走原 web_search.search（零行为变更）。
    if settings.WEB_SEARCH_DECOMPOSE:
        out = search_intel.search_decomposed(req.query, req.top_k, fetch_top=req.fetch_top)
    else:
        out = web_search.search(req.query, req.top_k, fetch_top=req.fetch_top)
    audit.record("web_search", caller, target=out["query"][:80],
                 result="ok" if out["ok"] and not out["degraded"] else "degraded",
                 meta={"provider": out["provider"], "mode": out["mode"],
                       "hits": len(out["hits"]), "degraded": out["degraded"],
                       "error": out["error"][:160], "elapsed_ms": out["elapsed_ms"]},
                 trace_id=trace_id)
    return {"code": 0, "data": out}


@router.get("/status")
async def search_status(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": web_search.status()}
