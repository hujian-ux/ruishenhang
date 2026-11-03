"""M16 · 文件进出路由（P0：xlsx 上传 → 按技能包执行 → 产出物下载）。

契约：
- POST   /agent/files                上传输入件（multipart），回 file_id + 结构预览
- GET    /agent/files                可见文件列表（kind=upload|artifact，可按 run_id 精确定位）
- GET    /agent/files/stats          存储健康度（MinIO 是否在线 / 文件量与字节数）
- GET    /agent/files/{file_id}      下载原件或产出物（后端代理，带鉴权，不经浏览器直连 MinIO）
- GET    /agent/files/{file_id}/preview  表格结构预览（表头/列画像/前 N 行）
- DELETE /agent/files/{file_id}      删除（仅上传者本人；产出物随运行保留）

设计取舍：下载走后端代理而不是 MinIO 预签名 URL —— 预签名要暴露 MinIO 端口给
浏览器、还要配 CORS，对「内网部署 + 前端拿不到 MinIO 地址」的场景是负担。
文件最大 20MB，代理流式回传完全够用，且鉴权与 RBAC 在一条链路上。
"""
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.services import audit, file_store, object_store, tabular

router = APIRouter(prefix="/agent/files", tags=["files"])

ALLOWED_EXT = {"xlsx", "xlsm", "csv", "tsv", "txt", "md", "markdown", "json"}


def _require(file_id: str, caller: CallerContext):
    """取文件并做可见性判定。不区分「不存在」与「无权」——避免靠枚举探测他人文件。"""
    rec = file_store.get(file_id)
    if not rec or not file_store.can_access(rec, caller):
        raise HTTPException(status_code=404, detail=f"文件不存在或无权访问：{file_id}")
    return rec


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    caller: CallerContext = Depends(get_caller),
    trace_id: str = Depends(get_trace_id),
):
    """上传技能包的输入件（xlsx/csv/txt）。返回 file_id 供 /agent/invoke 引用。"""
    name = file.filename or "upload.xlsx"
    ext = tabular.ext_of(name)
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型：.{ext}（支持 {'/'.join(sorted(ALLOWED_EXT))}）",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(raw) > file_store.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大：{len(raw) / 1024 / 1024:.1f}MB（上限 "
                   f"{file_store.MAX_UPLOAD_BYTES // 1024 // 1024}MB）",
        )

    # 上传即解析：解析失败也照常入库（用户可能只是想存个附件），
    # 但把错误回给前端，让人当场知道"这份表读不出来"，而不是等到执行时才炸。
    parsed = tabular.parse(raw, name)
    store_ok = object_store.available()
    rec = file_store.put(
        raw, name, caller, kind=file_store.KIND_UPLOAD, trace_id=trace_id,
        meta={
            "parse_ok": parsed.get("ok", False),
            "parse_error": parsed.get("error") if not parsed.get("ok") else "",
            "sheets": parsed.get("sheet_count", 0),
            "rows": parsed.get("total_rows", 0),
            "store": "minio" if store_ok else "memory",
        },
    )
    audit.record("upload", caller, target=rec["file_id"], result="ok",
                 meta={"filename": name, "size": len(raw), "parse_ok": parsed.get("ok", False)},
                 trace_id=trace_id)
    return {
        "code": 0,
        "data": {
            **file_store.public_view(rec),
            "preview": tabular.to_preview(parsed),
            "object_store_available": store_ok,
        },
    }


@router.get("")
async def list_files(
    kind: str = Query("", description="upload|artifact，空为全部"),
    run_id: str = Query("", description="按某次执行过滤（取产出物）"),
    agent_id: str = Query("", description="按智能体过滤"),
    limit: int = Query(50, ge=1, le=200),
    caller: CallerContext = Depends(get_caller),
):
    items = file_store.list_files(caller, kind=kind, run_id=run_id,
                                  agent_id=agent_id, limit=limit)
    return {"code": 0, "data": {
        "files": [file_store.public_view(r) for r in items],
        "count": len(items),
        "stats": file_store.stats(),
    }}


@router.get("/stats")
async def files_stats(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {
        **file_store.stats(),
        "object_store_available": object_store.available(),
        "max_upload_mb": file_store.MAX_UPLOAD_BYTES // 1024 // 1024,
    }}


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    caller: CallerContext = Depends(get_caller),
    trace_id: str = Depends(get_trace_id),
):
    rec = _require(file_id, caller)
    data = file_store.read(rec)
    if data is None:
        raise HTTPException(status_code=404,
                            detail=f"文件内容已丢失（对象存储不可达或已清理）：{file_id}")
    audit.record("download", caller, target=file_id, result="ok",
                 meta={"filename": rec.get("filename"), "kind": rec.get("kind"),
                       "size": rec.get("size")}, trace_id=trace_id)
    # 中文文件名必须走 filename*，否则部分浏览器会拿到乱码或丢掉扩展名
    from urllib.parse import quote
    fname = rec.get("filename") or "download"
    disp = f"attachment; filename=\"{quote(fname)}\"; filename*=UTF-8''{quote(fname)}"
    return StreamingResponse(BytesIO(data), media_type=rec.get("content_type"),
                             headers={"Content-Disposition": disp,
                                      "Content-Length": str(len(data))})


@router.get("/{file_id}/preview")
async def preview_file(file_id: str, caller: CallerContext = Depends(get_caller)):
    rec = _require(file_id, caller)
    data = file_store.read(rec)
    if data is None:
        raise HTTPException(status_code=404, detail="文件内容已丢失")
    return {"code": 0, "data": tabular.to_preview(tabular.parse(data, rec["filename"]))}


@router.delete("/{file_id}")
async def delete_file(file_id: str, caller: CallerContext = Depends(get_caller)):
    rec = _require(file_id, caller)
    if rec.get("owner_id") != caller.userId and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅上传者本人可删除")
    file_store.delete(file_id)
    return {"code": 0, "data": {"file_id": file_id, "deleted": True}}
