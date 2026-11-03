"""M13 审计日志：记录关键动作的「谁 / 何时 / 做了什么 / 结果」。

与计量（usage_records）解耦——计量用于对账计费，审计用于合规追溯：
「谁在什么时候查了什么、下载了哪份原文、做了哪个审批决策」。

存储策略与计量同源降级：
  - PG 可达 → 落库 audit_logs（jsonb 载荷，免迁移约束）
  - PG 不可达 → SQLite 文件兜底（tests / 离线演示），保证记录不丢

四类动作（业务层显式调用 record）：
  - retrieve   : 知识库检索（命中哪些文档、来源身份）
  - generate   : 智能体编排生成（agent_id、是否命中知识）
  - download   : 知识原文读回（doc_id / object_key，最敏感）
  - approval   : 审批决策（approval_id / decision）
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext

# ---- 存储后端（PG 优先，SQLite 兜底） ---------------------------------------

_pg_conn = None
_pg_checked = False
_local = threading.local()


def _connect_pg():
    global _pg_conn, _pg_checked
    if _pg_checked:
        return _pg_conn
    _pg_checked = True
    try:
        import psycopg2  # type: ignore
        c = psycopg2.connect(
            host=settings.PG_HOST, port=settings.PG_PORT, dbname=settings.PG_DB,
            user=settings.PG_USER, password=settings.PG_PASSWORD, connect_timeout=2,
        )
        c.autocommit = True
        _ensure_pg(c)
        _pg_conn = c
    except Exception:
        _pg_conn = None
    return _pg_conn


def available() -> bool:
    """当前审计后端是否可用（PG 或 SQLite）。"""
    if _connect_pg() is not None:
        return True
    try:
        _sqlite_conn()  # 触发建表
        return True
    except Exception:
        return False


def _ensure_pg(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id BIGSERIAL PRIMARY KEY,
                trace_id TEXT,
                tenant_id TEXT NOT NULL,
                dept_id TEXT,
                user_id TEXT,
                role TEXT,
                action TEXT NOT NULL,
                target TEXT,
                result TEXT,
                meta_json JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_logs (tenant_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_user_ts ON audit_logs (user_id, created_at);
        """)


def _sqlite_conn():
    db_path = settings.EMBED_CACHE_PATH.replace("embed_cache.db", "audit.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT, tenant_id TEXT, dept_id TEXT, user_id TEXT, role TEXT,
            action TEXT, target TEXT, result TEXT, meta_json TEXT, created_at TEXT
        );
    """)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- 写入 -------------------------------------------------------------------

def record(action: str, caller: CallerContext, target: str = "", result: str = "ok",
          meta: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None) -> str:
    """记录一条审计事件，返回 trace_id。meta 会被 JSON 化且脱敏（不存原文正文）。"""
    if trace_id is None:
        trace_id = getattr(_local, "trace_id", None) or uuid.uuid4().hex[:16]
    meta_json = json.dumps(_sanitize(meta or {}), ensure_ascii=False)
    pg = _connect_pg()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_logs
                       (trace_id, tenant_id, dept_id, user_id, role, action, target, result, meta_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (trace_id, caller.tenantId, caller.deptId, caller.userId,
                     caller.role, action, target, result, meta_json),
                )
            return trace_id
        except Exception:
            pass  # 落到 SQLite 兜底
    # SQLite 兜底
    try:
        conn = _sqlite_conn()
        conn.execute(
            """INSERT INTO audit_logs
               (trace_id, tenant_id, dept_id, user_id, role, action, target, result, meta_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (trace_id, caller.tenantId, caller.deptId, caller.userId,
             caller.role, action, target, result, meta_json, _now_iso()),
        )
        conn.commit()
    except Exception:
        pass
    return trace_id


def _sanitize(meta: Dict[str, Any]) -> Dict[str, Any]:
    """审计不记录敏感字段（原文正文、令牌等），只留可溯源的元数据。"""
    blocked = {"content", "text", "body", "token", "password", "secret", "api_key"}
    return {k: v for k, v in meta.items() if k.lower() not in blocked}


# ---- 查询（RBAC） -----------------------------------------------------------

def get_logs(caller: CallerContext, action: Optional[str] = None,
             limit: int = 100) -> List[Dict[str, Any]]:
    """按身份收敛的审计查询：
      platform_admin : 看全部
      dept_admin     : 仅本部门
      employee       : 仅本人
    """
    pg = _connect_pg()
    rows: List[Dict[str, Any]] = []
    try:
        if pg is not None:
            sql = """SELECT trace_id, tenant_id, dept_id, user_id, role, action,
                            target, result, meta_json, created_at
                     FROM audit_logs WHERE 1=1"""
            params: List[Any] = []
            if caller.role == "dept_admin":
                sql += " AND dept_id=%s"; params.append(caller.deptId)
            elif caller.role == "employee":
                sql += " AND user_id=%s"; params.append(caller.userId)
            if action:
                sql += " AND action=%s"; params.append(action)
            sql += " ORDER BY created_at DESC LIMIT %s"; params.append(limit)
            with pg.cursor() as cur:
                cur.execute(sql, params)
                for r in cur.fetchall():
                    rows.append(_row_to_dict(r))
        else:
            conn = _sqlite_conn()
            sql = """SELECT trace_id, tenant_id, dept_id, user_id, role, action,
                            target, result, meta_json, created_at FROM audit_logs WHERE 1=1"""
            params = []
            if caller.role == "dept_admin":
                sql += " AND dept_id=?"; params.append(caller.deptId)
            elif caller.role == "employee":
                sql += " AND user_id=?"; params.append(caller.userId)
            if action:
                sql += " AND action=?"; params.append(action)
            sql += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
            for r in conn.execute(sql, params).fetchall():
                rows.append(_row_to_dict(r, sqlite=True))
    except Exception:
        rows = []
    return rows


def _row_to_dict(r, sqlite: bool = False) -> Dict[str, Any]:
    cols = ["trace_id", "tenant_id", "dept_id", "user_id", "role",
            "action", "target", "result", "meta_json", "created_at"]
    d = dict(zip(cols, r))
    if isinstance(d.get("meta_json"), str):
        try:
            d["meta_json"] = json.loads(d["meta_json"])
        except Exception:
            pass
    return d
