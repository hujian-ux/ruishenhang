"""M6 PostgreSQL 持久层（会话/审批单写穿 + 启动加载）。

docker-compose 的 postgres 就绪时自动启用（psycopg2/psycopg 任一）；
不可达时 available()=False，业务层继续用内存兜底，测试与离线演示不受影响。
表结构见 infra/init/02_persistence.sql（jsonb 载荷，免迁移约束）。
"""
import json
from typing import Any, Dict, List

from app.core.config import settings

_conn = None
_checked = False


def _connect():
    global _conn, _checked
    if _checked:
        return _conn
    _checked = True
    try:
        import psycopg2  # type: ignore
        _conn = psycopg2.connect(
            host=settings.PG_HOST, port=settings.PG_PORT, dbname=settings.PG_DB,
            user=settings.PG_USER, password=settings.PG_PASSWORD, connect_timeout=2,
        )
        _conn.autocommit = True
        _ensure_tables()
    except Exception:
        _conn = None
    return _conn


def _ensure_tables() -> None:
    with _conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS approval_tickets (
                approval_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS usage_records (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                dept_id TEXT,
                user_id TEXT,
                agent_id TEXT,
                model_id TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cost DOUBLE PRECISION DEFAULT 0,
                real BOOLEAN,
                latency_ms INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now()
            );
            ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS real BOOLEAN;
            ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS latency_ms INTEGER DEFAULT 0;
            CREATE INDEX IF NOT EXISTS idx_usage_dim ON usage_records (tenant_id, dept_id, created_at);
            -- M8 试点：知识库文档注册表（重启不清零，200–500 篇语料不必每次重灌）
            -- 注意：kb_documents 在 infra/init/01_acl.sql 里已有旧定义（owner/dept/qdrant_id/minio_key），
            -- 这里不重建表，只把 M8 需要的列 ALTER 补上，两套语义共用一张表。
            CREATE TABLE IF NOT EXISTS kb_documents (
                doc_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                title TEXT,
                scope TEXT,
                owner_id TEXT,
                dept_id TEXT,
                source TEXT,
                filename TEXT,
                content_hash TEXT,
                chars INTEGER DEFAULT 0,
                object_key TEXT,
                content TEXT,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS doc_id TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS owner_id TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS dept_id TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS source TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS filename TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS chars INTEGER DEFAULT 0;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS object_key TEXT;
            ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
            CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_doc_id ON kb_documents (doc_id);
            CREATE INDEX IF NOT EXISTS idx_kb_hash ON kb_documents (tenant_id, content_hash);
            CREATE INDEX IF NOT EXISTS idx_kb_dept_m8 ON kb_documents (tenant_id, dept_id, scope);
        """)


def available() -> bool:
    return _connect() is not None


_KEY_COL = {"agent_sessions": "session_id", "approval_tickets": "approval_id"}


def upsert(table: str, key: str, payload: Dict[str, Any]) -> bool:
    """写穿：内存为主存，PG 为持久副本。失败静默（离线/抖动不阻断业务）。"""
    conn = _connect()
    if not conn or table not in _KEY_COL:
        return False
    try:
        col = _KEY_COL[table]
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} ({col}, payload) VALUES (%s, %s) "
                f"ON CONFLICT ({col}) DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()",
                (key, json.dumps(payload, ensure_ascii=False)),
            )
        return True
    except Exception:
        return False


def load_all(table: str) -> List[Dict[str, Any]]:
    """启动时把持久化数据载回内存（重启不丢会话/审批单）。"""
    conn = _connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {table}")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# M7 通用行操作（usage_records 计量用；表名走白名单，防 SQL 注入）
# ─────────────────────────────────────────────────────────────────────────────
def insert_usage(rec: Dict[str, Any]) -> bool:
    """追加一条用量记录（真实 PG 行，可出账单）。失败静默，内存仍留副本。"""
    conn = _connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO usage_records (tenant_id, dept_id, user_id, agent_id, model_id, "
                "prompt_tokens, completion_tokens, cost, real, latency_ms) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rec.get("tenant_id"), rec.get("dept_id"), rec.get("user_id"),
                 rec.get("agent_id"), rec.get("model_id"),
                 int(rec.get("prompt_tokens") or 0), int(rec.get("completion_tokens") or 0),
                 float(rec.get("cost") or 0), bool(rec.get("real")),
                 int(rec.get("latency_ms") or 0)),
            )
        return True
    except Exception:
        return False


def load_usage(limit: int = 5000) -> List[Dict[str, Any]]:
    """启动载回最近的用量记录（重启后账单不丢）。"""
    conn = _connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id, dept_id, user_id, agent_id, model_id, "
                "prompt_tokens, completion_tokens, cost, real, latency_ms, "
                "EXTRACT(EPOCH FROM created_at) FROM usage_records "
                "ORDER BY id DESC LIMIT %s", (limit,))
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "tenant_id": r[0], "dept_id": r[1], "user_id": r[2], "agent_id": r[3],
                    "model_id": r[4], "prompt_tokens": int(r[5] or 0),
                    "completion_tokens": int(r[6] or 0), "cost": float(r[7] or 0),
                    "real": bool(r[8]) if r[8] is not None else None,
                    "latency_ms": int(r[9] or 0),
                    "ts": float(r[10] or 0),
                })
            return rows
    except Exception:
        return []


def delete_row(table: str, key_col: str, key: str) -> bool:
    """按主键删除一行（会话删除用）。表/列名白名单。"""
    if table not in _KEY_COL:
        return False
    conn = _connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE {key_col} = %s", (key,))
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# M8 知识库文档（试点跑两周的前提：灌进去的语料重启不丢）
# ─────────────────────────────────────────────────────────────────────────────
_KB_COLS = ("doc_id", "tenant_id", "title", "scope", "owner_id", "dept_id",
            "source", "filename", "content_hash", "chars", "object_key", "content")

# 插入时同时写 01_acl.sql 的旧列（owner/dept/minio_key/created_by），
# 让「ACL 元数据表」与「运行时可载回表」共用一行、语义一致。
_KB_INSERT_COLS = ("doc_id", "tenant_id", "title", "scope", "owner_id", "dept_id",
                   "owner", "dept", "source", "filename", "content_hash", "chars",
                   "object_key", "minio_key", "content", "created_by")


def kb_upsert(doc: Dict[str, Any]) -> bool:
    """写入/更新一篇知识文档（含原文，供重启载回后重建检索索引）。"""
    conn = _connect()
    if not conn:
        return False
    try:
        owner, dept = doc.get("owner_id"), doc.get("dept_id")
        vals = (
            doc.get("doc_id"), doc.get("tenant_id"), doc.get("title"), doc.get("scope"),
            owner, dept, owner, dept,
            doc.get("source"), doc.get("filename"), doc.get("content_hash"),
            int(doc.get("chars") or 0), doc.get("object_key"), doc.get("object_key"),
            doc.get("content"), owner,
        )
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in _KB_INSERT_COLS if c != "doc_id")
        sql = (f"INSERT INTO kb_documents ({', '.join(_KB_INSERT_COLS)}) "
               f"VALUES ({', '.join(['%s'] * len(_KB_INSERT_COLS))}) "
               f"ON CONFLICT (doc_id) DO UPDATE SET {upd}, updated_at = now()")
        with conn.cursor() as cur:
            cur.execute(sql, vals)
        return True
    except Exception:
        return False


def kb_load_all() -> List[Dict[str, Any]]:
    """启动载回全部知识文档（按写入顺序，便于索引重建稳定）。"""
    conn = _connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, tenant_id, title, scope, owner_id, dept_id, source, "
                "filename, content_hash, chars, object_key, content FROM kb_documents "
                "ORDER BY updated_at ASC")
            return [dict(zip(_KB_COLS, r)) | {"chars": int(r[9] or 0)} for r in cur.fetchall()]
    except Exception:
        return []


def kb_delete(doc_id: str) -> bool:
    conn = _connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kb_documents WHERE doc_id = %s", (doc_id,))
        return True
    except Exception:
        return False


def kb_has_hash(tenant_id: str, content_hash: str) -> bool:
    """同租户内同内容判重（批量导入幂等的持久依据）。"""
    conn = _connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM kb_documents WHERE tenant_id = %s AND content_hash = %s LIMIT 1",
                        (tenant_id, content_hash))
            return cur.fetchone() is not None
    except Exception:
        return False


def kb_hash_ids(tenant_id: str) -> Dict[str, str]:
    """启动时取 (hash → doc_id) 映射，避免重复导入历史语料。"""
    conn = _connect()
    if not conn:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content_hash, doc_id FROM kb_documents "
                        "WHERE tenant_id = %s AND content_hash IS NOT NULL", (tenant_id,))
            return {h: d for h, d in cur.fetchall() if h}
    except Exception:
        return {}


def kb_get_by_hash(tenant_id: str, content_hash: str) -> Dict[str, Any] | None:
    """按内容指纹精确取一篇（避免为了判重把全表读进内存）。"""
    conn = _connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, tenant_id, title, scope, owner_id, dept_id, source, "
                "filename, content_hash, chars, object_key, content FROM kb_documents "
                "WHERE tenant_id = %s AND content_hash = %s LIMIT 1",
                (tenant_id, content_hash))
            r = cur.fetchone()
            if not r:
                return None
            d = dict(zip(_KB_COLS, r))
            d["chars"] = int(d.get("chars") or 0)
            d["storage"] = "postgres"
            d["indexed"] = True
            return d
    except Exception:
        return None
