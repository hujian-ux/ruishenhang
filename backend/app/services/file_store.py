"""M16 · 文件注册表（P0 文件进出的元信息层）。

分工：
- **字节流** 走 `object_store`（MinIO，不可达时内存兜底）
- **元信息** 走本模块（内存 + PG 写穿），重启后靠 object_key 仍能取回文件

两种 kind：
- `upload`   —— 用户上传的输入件（xlsx/csv/txt），归属上传者
- `artifact` —— 技能包执行后生成的产出物（xlsx），归属本次调用的调用者

权限沿用第18章同一套语义（不做第二套规则）：
platform_admin 全量 / dept_admin 本部门 / 员工仅本人 —— 文件比文档更敏感，
**不设 global 概念**，部门管理员也看不到别的部门的文件。
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from app.services import object_store, pg_store

_TABLE = "agent_files"
_FILES: Dict[str, Dict[str, Any]] = {}

KIND_UPLOAD = "upload"
KIND_ARTIFACT = "artifact"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024     # 单文件 20MB（xlsx 台账量级足够）

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    # M17 多格式产出物
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
}


def content_type_of(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return CONTENT_TYPES.get(ext, "application/octet-stream")


def _safe_name(filename: str) -> str:
    """对象键里只用安全字符（原文件名存元信息，下载时还原）。"""
    base = os.path.basename(filename or "file")
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    stem = "".join(c for c in base.rsplit(".", 1)[0] if c.isalnum() or c in "-_")[:40] or "file"
    return f"{stem}.{ext}" if ext else stem


def _save(rec: Dict[str, Any]) -> Dict[str, Any]:
    rec["updated_at"] = time.time()
    _FILES[rec["file_id"]] = rec
    pg_store.upsert(_TABLE, rec["file_id"], rec)
    return rec


def put(data: bytes, filename: str, caller, kind: str = KIND_UPLOAD,
        agent_id: str = "", run_id: str = "", trace_id: str = "",
        meta: Optional[Dict[str, Any]] = None,
        reuse_same_hash: bool = True) -> Dict[str, Any]:
    """写入一份文件：算 sha256、落对象存储、登记元信息，返回记录。

    `reuse_same_hash`：同一用户重复上传同一份文件时复用原记录（避免 MinIO 里
    堆满同一份 5MB 台账的副本，也让「重新上传后再执行」不必重算解析）。
    """
    digest = hashlib.sha256(data).hexdigest()
    if reuse_same_hash and kind == KIND_UPLOAD:
        for r in _FILES.values():
            if (r.get("kind") == KIND_UPLOAD and r.get("sha256") == digest
                    and r.get("owner_id") == caller.userId):
                return r

    prefix = "file" if kind == KIND_UPLOAD else "art"
    file_id = f"{prefix}_{uuid.uuid4().hex[:12]}"
    key = f"agent-files/{caller.deptId or 'default'}/{file_id}/{_safe_name(filename)}"
    location = object_store.put_bytes(key, data, content_type_of(filename),
                                      metadata={"owner": caller.userId[:64]})
    rec = {
        "file_id": file_id,
        "kind": kind,
        "filename": os.path.basename(filename or "file"),
        "object_key": key,
        "location": location,
        "size": len(data),
        "sha256": digest,
        "content_type": content_type_of(filename),
        "owner_id": caller.userId,
        "dept_id": caller.deptId,
        "tenant_id": getattr(caller, "tenantId", ""),
        "agent_id": agent_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "created_at": time.time(),
        "meta": meta or {},
    }
    return _save(rec)


def get(file_id: str) -> Optional[Dict[str, Any]]:
    return _FILES.get(file_id)


def read(rec: Dict[str, Any]) -> Optional[bytes]:
    return object_store.get_bytes(rec["object_key"])


def can_access(rec: Dict[str, Any], caller) -> bool:
    """第18章权限语义：管理员看全域、部门管理员看本部门、员工看本人。
    H1（安全加固）：跨租户拦截——非平台管理员不得访问其它租户的文件（即便 owner/dept 同名）。
    平台级资产（tenant_id 留空）对所有租户可见，与全局共享语义一致。"""
    if caller.role == "platform_admin":
        return True
    rec_tenant = rec.get("tenant_id") or ""
    caller_tenant = getattr(caller, "tenantId", "") or ""
    if rec_tenant and rec_tenant != caller_tenant:
        return False
    if rec.get("owner_id") == caller.userId:
        return True
    if caller.role == "dept_admin" and rec.get("dept_id") == caller.deptId:
        return True
    return False


def list_files(caller, kind: str = "", run_id: str = "", agent_id: str = "",
               limit: int = 50) -> List[Dict[str, Any]]:
    """列出可见文件（新的在前）。产出物可按 run_id 精确定位。"""
    items = [r for r in _FILES.values() if can_access(r, caller)]
    if kind:
        items = [r for r in items if r.get("kind") == kind]
    if run_id:
        items = [r for r in items if r.get("run_id") == run_id]
    if agent_id:
        items = [r for r in items if r.get("agent_id") == agent_id]
    items.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return items[:limit]


def public_view(rec: Dict[str, Any], base_path: str = "/api/v1/agent/files") -> Dict[str, Any]:
    """对外视图：不回 object_key/location（存储路径属内部实现）。"""
    return {
        "file_id": rec["file_id"],
        "kind": rec.get("kind"),
        "filename": rec.get("filename"),
        "size": rec.get("size", 0),
        "content_type": rec.get("content_type"),
        "sha256": rec.get("sha256"),
        "agent_id": rec.get("agent_id") or "",
        "run_id": rec.get("run_id") or "",
        "created_at": rec.get("created_at"),
        "meta": rec.get("meta") or {},
        "download_url": f"{base_path}/{rec['file_id']}",
    }


def delete(file_id: str) -> bool:
    """删除文件：对象存储清字节 + 内存摘除。

    PG 侧的元信息不硬删（审计要能追溯到"谁在什么时候传了什么、什么时候删的"），
    但内存与对象存储都没了，再次访问会正常返回 404。
    """
    rec = _FILES.pop(file_id, None)
    if not rec:
        return False
    object_store.delete_object(rec["object_key"])
    return True


def load_from_pg() -> int:
    """启动时把文件元信息载回内存（重启后产出物仍可下载）。"""
    n = 0
    for rec in pg_store.load_all(_TABLE):
        fid = rec.get("file_id")
        if fid and fid not in _FILES:
            _FILES[fid] = rec
            n += 1
    return n


def stats() -> Dict[str, int]:
    uploads = sum(1 for r in _FILES.values() if r.get("kind") == KIND_UPLOAD)
    artifacts = sum(1 for r in _FILES.values() if r.get("kind") == KIND_ARTIFACT)
    return {"total": len(_FILES), "uploads": uploads, "artifacts": artifacts,
            "bytes": sum(r.get("size", 0) for r in _FILES.values())}
