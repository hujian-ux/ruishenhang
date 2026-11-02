"""kb 路由：知识写入（个人/部门/集团），服务端强制回填权限。

M6 真实化：
- 文档落库（内存注册表，含原文），原文写 MinIO 对象存储（不可达时 memory:// 兜底）
- GET /kb/documents：按第18章权限可见性返回当前用户可见的文档
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.core.filter import visible
from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import KbDocumentRequest, KbDocumentResponse
from app.services import object_store

router = APIRouter(prefix="/kb", tags=["kb"])

# 文档注册表：doc_id → 记录（含原文；生产替换 Postgres kb_documents 表）
_DOCS: dict[str, dict] = {}


@router.post("/documents", response_model=KbDocumentResponse)
async def create_document(req: KbDocumentRequest, caller: CallerContext = Depends(get_caller)):
    # 越权写拦截：department 时 dept_id 必须等于 caller.deptId
    if req.scope == "department" and req.dept_id and req.dept_id != caller.deptId:
        raise HTTPException(status_code=403, detail="forbidden: dept_id 越权")
    if req.scope == "global" and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可写 global")
    # 服务端强制回填 owner / dept / tenant（前端传入仅展示）
    doc_id = "doc_" + uuid.uuid4().hex[:8]
    key = f"kb/{caller.tenantId}/{doc_id}.txt"
    location = object_store.put_text(key, req.content,
                                     metadata={"scope": req.scope, "owner": caller.userId,
                                               "dept": caller.deptId})
    _DOCS[doc_id] = {
        "doc_id": doc_id,
        "title": req.title,
        "content": req.content,
        "scope": req.scope,
        "owner_id": caller.userId,
        "dept_id": caller.deptId,
        "tenant_id": caller.tenantId,
        "object_key": key,
        "storage": location,          # minio://bucket/key 或 memory://key
        "indexed": False,             # 接入向量化管道后变 true
    }
    return {
        "code": 0,
        "data": {
            "doc_id": doc_id,
            "scope": req.scope,
            "owner_id": caller.userId,
            "dept_id": caller.deptId,
            "storage": location,
            "object_store_available": object_store.available(),
            "indexed": False,
        },
    }


@router.get("/documents")
async def list_documents(caller: CallerContext = Depends(get_caller)):
    """当前用户可见的文档（复用第18章过滤语义：meta 对齐内存语料结构）。"""
    items = []
    for d in _DOCS.values():
        meta = {"tenant_id": d["tenant_id"], "scope": d["scope"],
                "dept": d["dept_id"], "owner": d["owner_id"]}
        if visible(meta, caller):
            items.append({k: d[k] for k in
                          ("doc_id", "title", "scope", "owner_id", "dept_id", "storage", "indexed")})
    return {"code": 0, "data": {"documents": items, "count": len(items)}}


@router.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str, caller: CallerContext = Depends(get_caller)):
    """取文档原文（权限校验后从对象存储读回，验证写读闭环）。"""
    d = _DOCS.get(doc_id)
    if not d:
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")
    meta = {"tenant_id": d["tenant_id"], "scope": d["scope"],
            "dept": d["dept_id"], "owner": d["owner_id"]}
    if not visible(meta, caller):
        raise HTTPException(status_code=403, detail="forbidden: 无权访问该文档")
    content = object_store.get_text(d["object_key"]) or d["content"]
    return {"code": 0, "data": {"doc_id": doc_id, "title": d["title"], "content": content,
                                "storage": d["storage"]}}
