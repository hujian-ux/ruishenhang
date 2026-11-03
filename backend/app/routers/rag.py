"""rag 路由：带权召回（第36章核心，不可绕过）。"""
from fastapi import APIRouter, Depends

from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import RetrieveRequest, RetrieveResponse
from app.services import audit, knowledge

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest, caller: CallerContext = Depends(get_caller),
                   trace_id: str = Depends(get_trace_id)):
    hits, filtered_out = knowledge.retrieve(req.query, caller, top_k=req.top_k)
    audit.record("retrieve", caller, target=req.query[:80], result="ok",
                 meta={"top_k": req.top_k, "hits": len(hits), "filtered_out": filtered_out},
                 trace_id=trace_id)
    return {
        "code": 0,
        "data": {
            "query": req.query,
            "hits": hits,
            "filtered_out": filtered_out,
            "trace_id": trace_id,
        },
    }
