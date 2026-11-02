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
