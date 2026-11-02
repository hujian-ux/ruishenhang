"""kb 路由：知识写入（个人/部门/集团），服务端强制回填权限。

M6 真实化：文档落库 + 原文写对象存储（MinIO / S3 兼容，不可达时 memory 兜底）。
M7 上传真实化：
  - multipart 文件上传（txt/md/csv/json…），落对象存储 + 落库 + **入检索语料**，
    上传完即可被 /rag/retrieve 与智能体对话召回（同一份索引，不做两套）；
  - 删除同步清理三处（注册表 / 检索语料 / 对象存储），避免"删了还能召回"。
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.core.filter import visible
from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import KbDocumentRequest, KbDocumentResponse
from app.services import knowledge, object_store

router = APIRouter(prefix="/kb", tags=["kb"])

# 文档注册表：doc_id → 记录（含原文；生产替换 Postgres kb_documents 表）
_DOCS: dict[str, dict] = {}

MAX_UPLOAD_BYTES = 512 * 1024          # 单文件 512KB（演示用纯文本知识；大文件走批量导入）
INDEX_CHAR_CAP = 20000                 # 入索引截断（防止超长文件拖慢重建索引）
ALLOWED_EXT = {"txt", "md", "markdown", "csv", "json", "log", "yml", "yaml", "sql"}


def _guard_scope(scope: str, dept_id: str | None, caller: CallerContext) -> None:
    """越权写拦截（与第18章过滤点同一套语义）。"""
    if scope not in ("personal", "department", "global"):
        raise HTTPException(status_code=422, detail=f"非法 scope：{scope}（personal|department|global）")
    if scope == "department" and dept_id and dept_id != caller.deptId:
        raise HTTPException(status_code=403, detail="forbidden: dept_id 越权")
    if scope == "global" and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可写 global")


def _ingest(title: str, content: str, scope: str, dept_id: str | None,
            caller: CallerContext, source: str, filename: str = "") -> dict:
    """统一入库：写对象存储 + 入检索语料 + 登记（服务端强制回填 owner/dept/tenant）。"""
    dept = dept_id or caller.deptId
    indexed_text = content[:INDEX_CHAR_CAP]
    doc_id = knowledge.add_document(title, indexed_text, scope, caller.userId, dept, caller.tenantId)
    key = f"kb/{caller.tenantId}/{doc_id}.txt"
    location = object_store.put_text(key, content,
                                     metadata={"scope": scope, "owner": caller.userId, "dept": dept})
    _DOCS[doc_id] = {
        "doc_id": doc_id,
        "title": title,
        "content": content,
        "scope": scope,
        "owner_id": caller.userId,
        "dept_id": dept,
        "tenant_id": caller.tenantId,
        "object_key": key,
        "storage": location,
        "source": source,                 # manual | upload
        "filename": filename,
        "chars": len(content),
        "indexed_chars": len(indexed_text),
        "indexed": True,                  # 已入检索语料（可被召回）
    }
    return _DOCS[doc_id]


def _meta(d: dict) -> dict:
    return {"tenant_id": d["tenant_id"], "scope": d["scope"],
            "dept": d["dept_id"], "owner": d["owner_id"]}


def _public(d: dict, with_content: bool = False) -> dict:
    out = {k: d[k] for k in
           ("doc_id", "title", "scope", "owner_id", "dept_id", "storage",
            "source", "chars", "indexed")}
    if d.get("filename"):
        out["filename"] = d["filename"]
    if with_content:
        out["content"] = d["content"]
    return out


@router.post("/documents", response_model=KbDocumentResponse)
async def create_document(req: KbDocumentRequest, caller: CallerContext = Depends(get_caller)):
    """手工粘贴正文入库（M6 契约保留；M7 起同时入检索语料，可直接被召回）。"""
    _guard_scope(req.scope, req.dept_id, caller)
    d = _ingest(req.title, req.content, req.scope, req.dept_id, caller, source="manual")
    return {
        "code": 0,
        "data": {
            "doc_id": d["doc_id"],
            "scope": d["scope"],
            "owner_id": d["owner_id"],
            "dept_id": d["dept_id"],
            "storage": d["storage"],
            "object_store_available": object_store.available(),
            "indexed": d["indexed"],
            "chars": d["chars"],
        },
    }


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    scope: str = Form("personal"),
    dept_id: str = Form(""),
    caller: CallerContext = Depends(get_caller),
):
    """文件上传（multipart）：落对象存储 + 落库 + 入检索语料，一步到位可被召回。"""
    name = file.filename or "upload.txt"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型 .{ext or '?'}（当前支持 {', '.join(sorted(ALLOWED_EXT))}；"
                   f"PDF/Word 请先转成文本）",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"文件过大：{len(raw)} 字节（上限 {MAX_UPLOAD_BYTES}）")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail="无法解码为文本（仅支持 UTF-8 / GBK）")

    _guard_scope(scope, dept_id or None, caller)
    d = _ingest(title.strip() or name.rsplit(".", 1)[0], content, scope,
                dept_id or None, caller, source="upload", filename=name)
    return {"code": 0, "data": {**_public(d), "object_store_available": object_store.available()}}


@router.get("/documents")
async def list_documents(caller: CallerContext = Depends(get_caller)):
    """当前用户可见的文档（复用第18章过滤语义：meta 对齐内存语料结构）。"""
    items = [_public(d) for d in _DOCS.values() if visible(_meta(d), caller)]
    items.sort(key=lambda d: d["doc_id"], reverse=True)
    return {"code": 0, "data": {"documents": items, "count": len(items),
                                "object_store_available": object_store.available()}}


@router.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str, caller: CallerContext = Depends(get_caller)):
    """取文档原文（权限校验后从对象存储读回，验证写读闭环）。"""
    d = _DOCS.get(doc_id)
    if not d:
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")
    if not visible(_meta(d), caller):
        raise HTTPException(status_code=403, detail="forbidden: 无权访问该文档")
    content = object_store.get_text(d["object_key"]) or d["content"]
    return {"code": 0, "data": {"doc_id": doc_id, "title": d["title"], "content": content,
                                "storage": d["storage"], "chars": len(content)}}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, caller: CallerContext = Depends(get_caller)):
    """删除文档：注册表 + 检索语料 + 对象存储三处同步清理。"""
    d = _DOCS.get(doc_id)
    if not d:
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")
    if not visible(_meta(d), caller):
        raise HTTPException(status_code=403, detail="forbidden: 无权删除该文档")
    _DOCS.pop(doc_id, None)
    purged = knowledge.remove_document(doc_id)
    object_store.delete_text(d["object_key"])
    return {"code": 0, "data": {"doc_id": doc_id, "deleted": True,
                                "retrieval_purged": purged}}
