"""kb 路由：知识写入（个人/部门/集团），服务端强制回填权限。

M6 真实化：文档落库 + 原文写对象存储（MinIO / S3 兼容，不可达时 memory 兜底）。
M7 上传真实化：multipart 上传 → 对象存储 + 落库 + 入检索语料，上传完即可被召回。
M8 试点化（本文件变薄，逻辑下沉 services/kb_store.py）：
  - **多文件批量上传** `POST /kb/documents/bulk`：一次收多篇，服务端去重、只重建一次索引；
  - **内容去重**：同租户同内容不再重复入库（返回 duplicated + 既有 doc_id），
    重跑导入/误点两次都不会把语料稀释；
  - **语料画像** `GET /kb/stats`：篇数/字数/分布/截断/存储与 PG 状态，试点每天开工先看；
  - **PG 载回**：重启后知识库不清零（kb_store.load_from_pg）。
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import KbDocumentRequest, KbDocumentResponse
from app.services import kb_store, object_store

router = APIRouter(prefix="/kb", tags=["kb"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024        # 单文件 2MB（超长原文照存，入索引截断）
MAX_BULK_FILES = 50                       # 单次批量上传文件数（更大规模走目录导入 CLI）
ALLOWED_EXT = {"txt", "md", "markdown", "csv", "json", "log", "yml", "yaml", "sql", "html", "htm"}


def _guard_scope(scope: str, dept_id: str | None, caller: CallerContext) -> None:
    """越权写拦截（与第18章过滤点同一套语义）。"""
    if scope not in ("personal", "department", "global"):
        raise HTTPException(status_code=422, detail=f"非法 scope：{scope}（personal|department|global）")
    if scope == "department" and dept_id and dept_id != caller.deptId:
        raise HTTPException(status_code=403, detail="forbidden: dept_id 越权")
    if scope == "global" and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可写 global")


def _decode(file: UploadFile, raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415,
                                detail=f"无法解码为文本：{file.filename}（仅支持 UTF-8 / GBK）")


def _ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


async def _read_upload(file: UploadFile) -> tuple[str, str]:
    """校验扩展名/大小并解码，返回 (filename, content)。"""
    name = file.filename or "upload.txt"
    ext = _ext_of(name)
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型 .{ext or '?'}（支持 {', '.join(sorted(ALLOWED_EXT))}；"
                   f"PDF/Word 请先转成文本）",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail=f"文件为空：{name}")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"文件过大：{name} {len(raw)} 字节（上限 {MAX_UPLOAD_BYTES}）")
    return name, _decode(file, raw)


@router.post("/documents", response_model=KbDocumentResponse)
async def create_document(req: KbDocumentRequest, caller: CallerContext = Depends(get_caller)):
    """手工粘贴正文入库（M6 契约保留；M7 起同时入检索语料，可直接被召回）。"""
    _guard_scope(req.scope, req.dept_id, caller)
    d = kb_store.register(req.title, req.content, req.scope, caller,
                          dept_id=req.dept_id, source="manual")
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
    """单文件上传（multipart）：落对象存储 + 落库 + 入检索语料，一步到位可被召回。"""
    name, content = await _read_upload(file)
    _guard_scope(scope, dept_id or None, caller)
    d = kb_store.register(title.strip() or name.rsplit(".", 1)[0], content, scope, caller,
                          dept_id=dept_id or None, source="upload", filename=name)
    return {"code": 0, "data": {**d, "object_store_available": object_store.available()}}


@router.post("/documents/bulk")
async def bulk_upload_documents(
    files: list[UploadFile] = File(...),
    scope: str = Form("department"),
    dept_id: str = Form(""),
    caller: CallerContext = Depends(get_caller),
):
    """多文件批量上传（M8）：一次请求收多篇，服务端去重 + 只重建一次索引。

    返回逐文件结果（新增 / 重复 / 失败），试点导入 50 篇以内无需走 CLI。
    """
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")
    if len(files) > MAX_BULK_FILES:
        raise HTTPException(status_code=413,
                            detail=f"单次最多 {MAX_BULK_FILES} 个文件（更多请用 ingest_corpus CLI）")
    _guard_scope(scope, dept_id or None, caller)

    rows, failed = [], []
    for f in files:
        try:
            name, content = await _read_upload(f)
        except HTTPException as e:
            failed.append({"filename": f.filename, "reason": str(e.detail)})
            continue
        rows.append({"title": name.rsplit(".", 1)[0], "content": content, "filename": name})

    res = kb_store.register_many(rows, caller, scope=scope, dept_id=dept_id or None,
                                 source="bulk_upload")
    return {"code": 0, "data": {
        **res,
        "failed": failed,
        "skipped": len(failed),
        "scope": scope,
        "dept_id": dept_id or caller.deptId,
        "object_store_available": object_store.available(),
    }}


@router.get("/stats")
async def kb_stats(caller: CallerContext = Depends(get_caller)):
    """语料画像（试点每日开机第一屏）：规模 / 分布 / 截断 / 存储与持久化状态。"""
    return {"code": 0, "data": kb_store.stats()}


@router.get("/documents")
async def list_documents(caller: CallerContext = Depends(get_caller)):
    """当前用户可见的文档（复用第18章过滤语义）。"""
    items = kb_store.list_documents(caller)
    return {"code": 0, "data": {"documents": items, "count": len(items),
                                "object_store_available": object_store.available()}}


@router.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str, caller: CallerContext = Depends(get_caller)):
    """取文档原文（权限校验后从对象存储读回，验证写读闭环）。"""
    d = kb_store.readable(doc_id, caller)
    if not d:
        if kb_store.get(doc_id):
            raise HTTPException(status_code=403, detail="forbidden: 无权访问该文档")
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")
    content = object_store.get_text(d["object_key"]) or d["content"]
    return {"code": 0, "data": {"doc_id": doc_id, "title": d["title"], "content": content,
                                "storage": d["storage"], "chars": len(content)}}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, caller: CallerContext = Depends(get_caller)):
    """删除文档：注册表 + 检索语料 + 对象存储 + PG 四处同步清理。"""
    d = kb_store.readable(doc_id, caller)
    if not d:
        if kb_store.get(doc_id):
            raise HTTPException(status_code=403, detail="forbidden: 无权删除该文档")
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")
    kb_store.delete(doc_id)
    return {"code": 0, "data": {"doc_id": doc_id, "deleted": True, "retrieval_purged": True}}
