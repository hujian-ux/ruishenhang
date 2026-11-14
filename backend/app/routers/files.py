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
import base64
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.services import audit, file_store, object_store, tabular, vision

router = APIRouter(prefix="/agent/files", tags=["files"])

ALLOWED_EXT = {"xlsx", "xlsm", "csv", "tsv", "txt", "md", "markdown", "json"}


def _allowed_ext() -> set:
    """P1-10：图片上传白名单只在 VISION_ENABLED=1 时放开。

    默认配置下上传 .png 仍是 415（与 P1-10 之前逐字节一致），避免「开关没开、
    用户传了图、执行时却什么都没发生」这种最难解释的假成功。
    """
    if getattr(settings, "VISION_ENABLED", False):
        return ALLOWED_EXT | vision.IMAGE_EXT
    return ALLOWED_EXT


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
    allowed = _allowed_ext()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型：.{ext}（支持 {'/'.join(sorted(allowed))}）",
        )
    is_image = vision.is_image(name)
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
    # P1-10：图片是二进制，绝不能交给 tabular.parse（会把乱码塞进上下文），
    # 只登记格式与字节数，实际解读交给视觉通道。
    if is_image:
        fmt = vision.sniff(raw)
        parsed = {"ok": True, "kind": "image", "sheet_count": 0, "total_rows": 0}
        preview = {"ok": True, "kind": "image", "filename": name,
                   "size": len(raw), "format": fmt or ext,
                   "hint": "图片由视觉通道解读，不做表格解析",
                   "valid": bool(fmt)}
    else:
        parsed = tabular.parse(raw, name)
        preview = tabular.to_preview(parsed)
    store_ok = object_store.available()
    rec = file_store.put(
        raw, name, caller, kind=file_store.KIND_UPLOAD, trace_id=trace_id,
        meta={
            "parse_ok": parsed.get("ok", False),
            "parse_error": parsed.get("error") if not parsed.get("ok") else "",
            "sheets": parsed.get("sheet_count", 0),
            "rows": parsed.get("total_rows", 0),
            "store": "minio" if store_ok else "memory",
            "vision": is_image,
            "image_format": (vision.sniff(raw) or ext) if is_image else "",
        },
    )
    audit.record("upload", caller, target=rec["file_id"], result="ok",
                 meta={"filename": name, "size": len(raw), "parse_ok": parsed.get("ok", False),
                       "vision": is_image},
                 trace_id=trace_id)
    return {
        "code": 0,
        "data": {
            **file_store.public_view(rec),
            "preview": preview,
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


# ── P1-7 制品版本化与回滚 ──────────────────────────────────────────────────────
# 注意：下列以 /artifacts 开头的路由必须放在 /{file_id} 之前，否则会被 {file_id} 吞掉。
@router.get("/artifacts")
async def list_artifact_chains(
    session_id: str = Query("", description="按会话过滤（只列该会话的制品链）"),
    limit: int = Query(50, ge=1, le=200),
    caller: CallerContext = Depends(get_caller),
):
    """P1-7 制品版本链：把可见的产出物按 `会话ID::问题序号::格式` 聚合成链，
    返回每条链的最新版本、版本数与解析出的会话/问题序号/格式。回滚前先拉这个看有哪些链。"""
    chains = file_store.list_chains(caller, session_id=session_id, limit=limit)
    return {"code": 0, "data": {"chains": chains, "count": len(chains)}}


@router.get("/artifacts/{key}/versions")
async def list_artifact_versions(
    key: str,
    caller: CallerContext = Depends(get_caller),
):
    """P1-7 某条制品链的全部版本（降序，最新在前）。"""
    recs = file_store.versions(key, caller)
    if not recs:
        raise HTTPException(status_code=404, detail=f"制品链不存在或无权访问：{key}")
    return {"code": 0, "data": {
        "artifact_key": key,
        "versions": [file_store.public_view(r) for r in recs],
        "count": len(recs),
    }}


@router.post("/artifacts/{key}/rollback")
async def rollback_artifact(
    key: str,
    version: int = Query(..., description="要回滚到的目标版本号"),
    caller: CallerContext = Depends(get_caller),
    trace_id: str = Depends(get_trace_id),
):
    """P1-7 非破坏式回滚：把目标版本字节复制成一条「最新版本」（version=当前最大+1），
    旧版本全部保留，可再回滚回去。返回新建版本的 public_view。"""
    new_rec = file_store.rollback_version(key, version, caller)
    if not new_rec:
        raise HTTPException(
            status_code=404,
            detail=f"制品链或版本不存在或无权访问：{key} v{version}")
    audit.record("artifact_rollback", caller, target=new_rec["file_id"], result="ok",
                 meta={"artifact_key": key, "source_version": version,
                       "new_version": new_rec.get("version"),
                       "format": (new_rec.get("artifact_key") or "").split("::")[-1]},
                 trace_id=trace_id)
    return {"code": 0, "data": {
        "artifact": file_store.public_view(new_rec),
        "artifact_key": key,
        "rolled_back_from": version,
        "new_version": new_rec.get("version"),
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
    # P1-10：图片预览回 data URL。下载端点要带鉴权头，<img src> 直连会 401，
    # 前端拿不到缩略图；这里由后端代理出字节，避免为展示图片单独开匿名入口。
    if vision.is_image(rec.get("filename") or ""):
        fmt = vision.sniff(data)
        mime = vision.mime_of(fmt, rec.get("filename") or "") if fmt else rec.get("content_type")
        return {"code": 0, "data": {
            "ok": True, "kind": "image", "filename": rec.get("filename"),
            "size": len(data), "format": fmt or "", "valid": bool(fmt),
            "data_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        }}
    return {"code": 0, "data": tabular.to_preview(tabular.parse(data, rec["filename"]))}


@router.delete("/{file_id}")
async def delete_file(file_id: str, caller: CallerContext = Depends(get_caller)):
    rec = _require(file_id, caller)
    if rec.get("owner_id") != caller.userId and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅上传者本人可删除")
    file_store.delete(file_id)
    return {"code": 0, "data": {"file_id": file_id, "deleted": True}}
