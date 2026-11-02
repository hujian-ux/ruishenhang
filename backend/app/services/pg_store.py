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
