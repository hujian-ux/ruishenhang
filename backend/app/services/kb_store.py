"""知识库文档注册表（M8 从路由下沉为服务层，UI 上传递增与目录批量导入共用一套）。

三处一致（这是「上传即可召回」与「删了不再召回」的根因保证）：
  1. 检索语料（knowledge.PARENTS / 内存索引）——决定能不能被召回
  2. 对象存储（MinIO / S3 兼容，不可达时内存兜底）——决定原文能不能回读
  3. PG kb_documents —— 决定重启后还在不在（试点跑两周的前提）

两个 M8 关键能力：
  - **内容去重**：同租户 sha256 相同即视为同一篇，重跑导入幂等（不会灌出 500 份重复语料
    把召回质量稀释掉）。内存 hash 表命中优先，miss 再问 PG。
  - **启动载回**：PG 就绪时把全部文档灌回检索语料，**只重建一次索引**；
    PG 不可达时静默退化为纯内存（测试/离线演示不受影响）。
"""
import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.filter import visible
from app.models.caller import CallerContext
from app.services import knowledge, object_store, pg_store

# doc_id → 记录（含原文）
_DOCS: Dict[str, Dict[str, Any]] = {}
# (tenant_id, content_hash) → doc_id（去重快表）
_HASH: Dict[tuple, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# 基础
# ─────────────────────────────────────────────────────────────────────────────
def content_hash(text: str) -> str:
    """内容指纹（归一化空白后再算，避免复制粘贴的换行差异导致重复不入库）。"""
    norm = " ".join((text or "").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _meta(d: Dict[str, Any]) -> Dict[str, Any]:
    """对齐第18章单一过滤点所需的 meta 结构（与内存语料同形）。"""
    return {"tenant_id": d["tenant_id"], "scope": d["scope"],
            "dept": d["dept_id"], "owner": d["owner_id"]}


def public(d: Dict[str, Any], with_content: bool = False) -> Dict[str, Any]:
    out = {k: d.get(k) for k in
           ("doc_id", "title", "scope", "owner_id", "dept_id", "storage",
            "source", "chars", "indexed", "content_hash", "created_at")}
    if d.get("filename"):
        out["filename"] = d["filename"]
    if with_content:
        out["content"] = d["content"]
    return out


def existing_by_hash(tenant_id: str, h: str) -> Optional[Dict[str, Any]]:
    """同租户同内容是否已存在（内存优先，miss 再按指纹精确查 PG；不扫全表）。"""
    doc_id = _HASH.get((tenant_id, h))
    if doc_id and doc_id in _DOCS:
        return _DOCS[doc_id]
    if pg_store.available():
        row = pg_store.kb_get_by_hash(tenant_id, h)
        if row:
            _HASH[(tenant_id, h)] = row["doc_id"]
            return row
    return None


def get(doc_id: str) -> Optional[Dict[str, Any]]:
    return _DOCS.get(doc_id)


def readable(doc_id: str, caller: CallerContext) -> Optional[Dict[str, Any]]:
    """取文档并做可见性校验（不可见返回 None，由路由层转 403）。"""
    d = _DOCS.get(doc_id)
    if not d or not visible(_meta(d), caller):
        return None
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 写入
# ─────────────────────────────────────────────────────────────────────────────
def register(title: str, content: str, scope: str, caller: CallerContext,
             dept_id: Optional[str] = None, source: str = "manual",
             filename: str = "", allow_duplicate: bool = False,
             doc_id: Optional[str] = None) -> Dict[str, Any]:
    """入库一篇文档：去重 → 入检索语料 → 写对象存储 → 登记 → 落 PG。

    返回 public 字段 + duplicated 标记；重复时返回既有文档（不再写第二份）。
    """
    h = content_hash(content)
    dup = existing_by_hash(caller.tenantId, h)
    if dup and not allow_duplicate:
        return {**public(dup), "duplicated": True, "indexed": dup.get("indexed", True)}

    dept = dept_id or caller.deptId
    doc_id = doc_id or ("doc_" + uuid.uuid4().hex[:8])
    indexed_text = content[: settings.INGEST_INDEX_CHAR_CAP]
    knowledge.add_documents_bulk([{
        "doc_id": doc_id, "title": title, "content": indexed_text, "scope": scope,
        "owner": caller.userId, "dept": dept, "tenant_id": caller.tenantId, "source": source,
    }])
    key = f"kb/{caller.tenantId}/{doc_id}.txt"
    location = object_store.put_text(
        key, content, metadata={"scope": scope, "owner": caller.userId, "dept": dept})
    rec = {
        "doc_id": doc_id,
        "title": title,
        "content": content,
        "scope": scope,
        "owner_id": caller.userId,
        "dept_id": dept,
        "tenant_id": caller.tenantId,
        "object_key": key,
        "storage": location,
        "source": source,
        "filename": filename,
        "chars": len(content),
        "indexed_chars": len(indexed_text),
        "indexed": True,
        "content_hash": h,
        "created_at": time.time(),
    }
    _DOCS[doc_id] = rec
    _HASH[(caller.tenantId, h)] = doc_id
    pg_store.kb_upsert(rec)
    return {**public(rec), "duplicated": False}


def register_many(rows: List[Dict[str, Any]], caller: CallerContext,
                  scope: str, dept_id: Optional[str] = None,
                  source: str = "bulk") -> Dict[str, Any]:
    """批量入库（M8 批量导入核心）：去重 + **一次索引重建** + 逐篇落 PG。

    rows: [{title, content, filename?}]
    返回 {added, duplicated, indexed, docs:[...], chars_added}
    """
    added_rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    duplicated = 0
    local: Dict[str, str] = {}   # 本批内 hash → doc_id：同批重复（xxx-副本.md）也要判重
    for r in rows:
        content = r.get("content") or ""
        h = content_hash(content)
        if h in local:
            duplicated += 1
            results.append({"title": r.get("title"), "doc_id": local[h],
                            "duplicated": True, "filename": r.get("filename", "")})
            continue
        dup = existing_by_hash(caller.tenantId, h)
        if dup:
            duplicated += 1
            local[h] = dup["doc_id"]
            results.append({"title": r.get("title"), "doc_id": dup["doc_id"],
                            "duplicated": True, "filename": r.get("filename", "")})
            continue
        doc_id = "doc_" + uuid.uuid4().hex[:8]
        local[h] = doc_id
        added_rows.append({"doc_id": doc_id, "row": r, "hash": h})

    if added_rows:
        dept = dept_id or caller.deptId
        knowledge.add_documents_bulk([{
            "doc_id": a["doc_id"], "title": a["row"].get("title") or "(无标题)",
            "content": (a["row"].get("content") or "")[: settings.INGEST_INDEX_CHAR_CAP],
            "scope": scope, "owner": caller.userId, "dept": dept,
            "tenant_id": caller.tenantId, "source": source,
        } for a in added_rows])

    chars_added = 0
    for a in added_rows:
        r, doc_id, dept = a["row"], a["doc_id"], (dept_id or caller.deptId)
        content = r.get("content") or ""
        key = f"kb/{caller.tenantId}/{doc_id}.txt"
        location = object_store.put_text(
            key, content, metadata={"scope": scope, "owner": caller.userId, "dept": dept})
        rec = {
            "doc_id": doc_id, "title": r.get("title") or "(无标题)", "content": content,
            "scope": scope, "owner_id": caller.userId, "dept_id": dept,
            "tenant_id": caller.tenantId, "object_key": key, "storage": location,
            "source": source, "filename": r.get("filename", ""), "chars": len(content),
            "indexed_chars": len(content[: settings.INGEST_INDEX_CHAR_CAP]),
            "indexed": True, "content_hash": a["hash"], "created_at": time.time(),
        }
        _DOCS[doc_id] = rec
        _HASH[(caller.tenantId, a["hash"])] = doc_id
        chars_added += len(content)
        pg_store.kb_upsert(rec)
        results.append({"title": rec["title"], "doc_id": doc_id, "duplicated": False,
                        "filename": r.get("filename", ""), "chars": len(content)})

    return {"added": len(added_rows), "duplicated": duplicated, "total": len(rows),
            "chars_added": chars_added, "docs": results}


def delete(doc_id: str) -> bool:
    """删除：注册表 / 检索语料 / 对象存储 / PG 四处同步。"""
    d = _DOCS.pop(doc_id, None)
    if not d:
        return False
    knowledge.remove_document(doc_id)
    object_store.delete_text(d["object_key"])
    if d.get("content_hash"):
        _HASH.pop((d["tenant_id"], d["content_hash"]), None)
    pg_store.kb_delete(doc_id)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 读取 / 统计 / 载回
# ─────────────────────────────────────────────────────────────────────────────
def list_documents(caller: CallerContext) -> List[Dict[str, Any]]:
    items = [public(d) for d in _DOCS.values() if visible(_meta(d), caller)]
    items.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return items


def stats() -> Dict[str, Any]:
    """语料规模画像：试点第一天/每天开机先看这一屏，判断语料够不够。"""
    docs = list(_DOCS.values())
    by_scope: Dict[str, int] = {}
    by_dept: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    total_chars = 0
    truncated = 0
    cap = settings.INGEST_INDEX_CHAR_CAP
    for d in docs:
        by_scope[d["scope"]] = by_scope.get(d["scope"], 0) + 1
        by_dept[d["dept_id"] or "-"] = by_dept.get(d["dept_id"] or "-", 0) + 1
        by_source[d["source"]] = by_source.get(d["source"], 0) + 1
        total_chars += int(d.get("chars") or 0)
        if int(d.get("chars") or 0) > cap:
            truncated += 1
    return {
        "documents": len(docs),
        "total_chars": total_chars,
        "avg_chars": round(total_chars / len(docs), 1) if docs else 0,
        "by_scope": by_scope,
        "by_dept": by_dept,
        "by_source": by_source,
        "truncated_documents": truncated,       # 超长被截断入索引的篇数（原文完整保留）
        "unique_hashes": len(_HASH),
        "index_char_cap": cap,
        "object_store_available": object_store.available(),
        "pg_available": pg_store.available(),
        "pg_persisted": pg_store.available() and len(docs) > 0,
    }


def load_from_pg() -> int:
    """启动载回：PG 里的文档灌回检索语料（只重建一次索引）。PG 不可达返回 0。"""
    rows = pg_store.kb_load_all()
    if not rows:
        return 0
    fresh = [r for r in rows if r["doc_id"] not in _DOCS]
    for r in rows:
        rec = {
            "doc_id": r["doc_id"], "title": r.get("title") or "(无标题)",
            "content": r.get("content") or "", "scope": r.get("scope") or "personal",
            "owner_id": r.get("owner_id") or "", "dept_id": r.get("dept_id") or "",
            "tenant_id": r.get("tenant_id") or "", "object_key": r.get("object_key") or "",
            "storage": "postgres", "source": r.get("source") or "restored",
            "filename": r.get("filename") or "", "chars": int(r.get("chars") or 0),
            "indexed_chars": len((r.get("content") or "")[: settings.INGEST_INDEX_CHAR_CAP]),
            "indexed": True, "content_hash": r.get("content_hash") or "",
            "created_at": time.time(),
        }
        _DOCS[rec["doc_id"]] = rec
        if rec["content_hash"]:
            _HASH[(rec["tenant_id"], rec["content_hash"])] = rec["doc_id"]

    if fresh:
        knowledge.add_documents_bulk([{
            "doc_id": r["doc_id"], "title": r.get("title") or "(无标题)",
            "content": (r.get("content") or "")[: settings.INGEST_INDEX_CHAR_CAP],
            "scope": r.get("scope") or "personal", "owner": r.get("owner_id") or "",
            "dept": r.get("dept_id") or "", "tenant_id": r.get("tenant_id") or "",
            "source": "restored",
        } for r in fresh])
    return len(rows)


def reset() -> None:
    """清空注册表（测试用；不动 PG）。"""
    _DOCS.clear()
    _HASH.clear()
