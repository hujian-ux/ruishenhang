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

from app.core.config import settings
from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import KbDocumentRequest, KbDocumentResponse, KbIngestRequest
from app.services import audit, guardrails, ingest_jobs, kb_store, object_store

router = APIRouter(prefix="/kb", tags=["kb"])

# 最近一次入库护栏结论：给运维看「这条语料为什么被标 / 被拦」，不落库、不进索引
_LAST_GUARD: dict = {}


def _content_guard(content: str, scope: str, source: str,
                   caller: CallerContext, trace_id: str = "") -> dict:
    """第27章知识侧防护：来源可信度 + 入库内容投毒/注入扫描。

    默认只标记并留痕；GUARDRAILS_STRICT=1 且确实命中模式才拒绝入库。
    不改写正文（原文照存，避免影响去重与召回口径）。
    """
    global _LAST_GUARD
    trust = guardrails.check_source(source or "", scope)
    scan = guardrails.scan_input(content[:4000])
    hits = scan.get("hits") or []
    verdict = {
        "source_trust": trust,
        "content_risk": scan.get("risk", 0.0),
        "patterns": [h.get("kind", "") for h in hits],
        "action": "flag",
        "scope": scope,
    }
    blocked = bool(settings.GUARDRAILS_STRICT and scan.get("blocked") and hits)
    if blocked:
        verdict["action"] = "reject"
    _LAST_GUARD = verdict
    if hits or not trust.get("trusted") or blocked:
        audit.record("kb_write", caller, target=source or "manual",
                     result="blocked_poisoning" if blocked else "flagged",
                     meta={"patterns": verdict["patterns"], "risk": verdict["content_risk"],
                           "scope": scope, "action": verdict["action"]},
                     trace_id=trace_id or None)
    if blocked:
        raise HTTPException(status_code=400, detail="文档内容命中投毒/注入护栏，已拒绝入库")
    return verdict


@router.get("/guardrails/last")
async def last_ingest_guard(caller: CallerContext = Depends(get_caller)):
    """运维自检：最近一次入库的来源可信度与内容风险结论。"""
    return {"code": 0, "data": dict(_LAST_GUARD)}

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
    _content_guard(req.content, req.scope, "manual", caller)
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
    guard = _content_guard(content, scope, f"upload:{name}", caller)
    d = kb_store.register(title.strip() or name.rsplit(".", 1)[0], content, scope, caller,
                          dept_id=dept_id or None, source="upload", filename=name)
    return {"code": 0, "data": {**d, "object_store_available": object_store.available(),
                               "guardrails": guard}}


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
        _content_guard(content, scope, f"bulk_upload:{name}", caller)
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


# ── M10 异步导入：提交即返回，进度靠轮询 ────────────────────────────────────
@router.post("/ingest")
async def create_ingest_job(req: KbIngestRequest, caller: CallerContext = Depends(get_caller)):
    """提交异步导入任务（万篇级）。

    同步 `/kb/documents/bulk` 一次最多 50 个文件，1 万篇要分 200 次请求；
    这里一次提交整批，后台单 worker 分批入库，前端拿 `job_id` 轮询进度。
    已入库的文档不会因为任务中断而丢——任务单只在内存，数据在 PG。
    """
    _guard_scope(req.scope, req.dept_id, caller)
    rows = [{"title": d.title or (d.filename or "").rsplit(".", 1)[0] or "(无标题)",
             "content": d.content, "filename": d.filename or ""} for d in req.documents]
    if not rows:
        raise HTTPException(status_code=400, detail="documents 为空")
    _content_guard("\n".join(r["content"] for r in rows[:20])[:4000], req.scope,
                   req.source or "async_ingest", caller)
    try:
        job = ingest_jobs.submit(rows, caller, scope=req.scope,
                                 dept_id=req.dept_id, source=req.source or "async_ingest")
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return {"code": 0, "data": {**job, "object_store_available": object_store.available()}}


@router.get("/ingest")
async def list_ingest_jobs(limit: int = 20, caller: CallerContext = Depends(get_caller)):
    """最近导入任务（新→旧）：本人任务，平台管理员看全租户。"""
    jobs = ingest_jobs.list_jobs(caller, limit=max(1, min(limit, 100)))
    return {"code": 0, "data": {"jobs": jobs, "count": len(jobs)}}


@router.get("/ingest/{job_id}")
async def get_ingest_job(job_id: str, caller: CallerContext = Depends(get_caller)):
    """查导入进度：processed/total 单调递增，status=done 即全部入库完成。"""
    job = ingest_jobs.get(job_id, caller)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或无权查看")
    return {"code": 0, "data": job}


@router.post("/ingest/{job_id}/cancel")
async def cancel_ingest_job(job_id: str, caller: CallerContext = Depends(get_caller)):
    """取消导入（尽力而为）：当前批跑完就停，已入库的不回滚。"""
    if not ingest_jobs.cancel(job_id, caller):
        raise HTTPException(status_code=409, detail="任务不存在、已终态或无权取消")
    return {"code": 0, "data": {"job_id": job_id, "status": "cancelling"}}


@router.get("/index")
async def kb_index(caller: CallerContext = Depends(get_caller)):
    """索引健康（M9 规模化）：规模 / 构建耗时 / 查询延迟 / 向量后端 / 缓存命中。

    试点排障第一眼看的接口——「为什么今天回答变慢」「重启后多久能恢复」
    都能在这一屏回答，不用翻日志。
    """
    from app.services import knowledge, vector_store
    data = knowledge.index_health()
    data["vector_store"] = vector_store.stats()
    return {"code": 0, "data": data}


@router.get("/documents")
async def list_documents(caller: CallerContext = Depends(get_caller)):
    """当前用户可见的文档（复用第18章过滤语义）。"""
    items = kb_store.list_documents(caller)
    return {"code": 0, "data": {"documents": items, "count": len(items),
                                "object_store_available": object_store.available()}}


@router.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str, caller: CallerContext = Depends(get_caller),
                              trace_id: str = Depends(get_trace_id)):
    """取文档原文（权限校验后从对象存储读回，验证写读闭环）。"""
    d = kb_store.readable(doc_id, caller)
    if not d:
        if kb_store.get(doc_id):
            audit.record("download", caller, target=doc_id, result="error:forbidden", trace_id=trace_id)
            raise HTTPException(status_code=403, detail="forbidden: 无权访问该文档")
        audit.record("download", caller, target=doc_id, result="error:not_found", trace_id=trace_id)
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")
    content = object_store.get_text(d["object_key"]) or d["content"]
    audit.record("download", caller, target=doc_id, result="ok",
                 meta={"title": d.get("title"), "chars": len(content)}, trace_id=trace_id)
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
