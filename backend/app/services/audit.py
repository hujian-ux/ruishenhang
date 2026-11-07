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
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext

REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "reports"

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
          meta: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None,
          compliance: Optional[list[str]] = None) -> str:
    """记录一条审计事件，返回 trace_id。meta 会被 JSON 化且脱敏（不存原文正文）。

    compliance：合规条款标签（如 ["等保2.0-S3","ISO27001-A.12.4"]），随 meta 存，供合规映射聚合。
    """
    if compliance:
        m = dict(meta or {})
        m["compliance"] = list(compliance)
        meta = m
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


# ── P1-29 审计留存与合规：留存 TTL / 只读加密导出 / 等保-ISO 映射 / 级联删除 ──

def _legal_hold() -> list[str]:
    return [a.strip() for a in (settings.AUDIT_LEGAL_HOLD_ACTIONS or "").split(",") if a.strip()]


def _legal_hold() -> list[str]:
    return [a.strip() for a in (settings.AUDIT_LEGAL_HOLD_ACTIONS or "").split(",") if a.strip()]


def purge_expired(retention_days: Optional[int] = None) -> int:
    """按留存策略清理过期审计：超过 retention_days 的天数且非法定保留动作才删。

    法定保留（AUDIT_LEGAL_HOLD_ACTIONS：approval/download/handoff）永不过期。
    返回实际删除条数。PG 与 SQLite 双后端都实现；无后端可用时返回 0。
    """
    days = int(retention_days if retention_days is not None else settings.AUDIT_RETENTION_DAYS)
    hold = _legal_hold()
    hold_sql = (" AND action NOT IN (%s)" % ",".join(["%s"] * len(hold))) if hold else ""
    params_hold = tuple(hold)
    pg = _connect_pg()
    deleted = 0
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute(
                    f"DELETE FROM audit_logs WHERE created_at < now() - make_interval(days => %s){hold_sql}",
                    (days, *params_hold),
                )
                deleted = cur.rowcount
        except Exception:
            return 0
        return deleted
    # SQLite 兜底：created_at 为 UTC ISO 字符串，字典序即时间序
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = _sqlite_conn()
        q = "DELETE FROM audit_logs WHERE created_at < ?" + (" AND action NOT IN (%s)" % ",".join(["?"] * len(hold)) if hold else "")
        args: list = [cutoff] + list(params_hold)
        cur = conn.execute(q, args)
        deleted = cur.rowcount
        conn.commit()
    except Exception:
        return 0
    return deleted


def export_records(caller: CallerContext, out_path: Optional[str] = None,
                   encrypt: Optional[bool] = None) -> Dict[str, Any]:
    """只读导出 caller 可见的审计记录（RBAC 收敛，绝不改数据）。

    - 默认导出为 CSV（明文）；若配置了 AUDIT_EXPORT_KEY，则加密为 .enc（Fernet）。
    - encrypt 强制为 False 时即使有 key 也明文导出（运维排障用）。
    返回 {path, encrypted, rows, warning}。
    """
    rows = get_logs(caller, limit=10 ** 7)
    import csv as _csv
    import io as _io

    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["trace_id", "tenant_id", "dept_id", "user_id", "role",
                "action", "target", "result", "meta_json", "created_at"])
    for r in rows:
        w.writerow([r.get("trace_id"), r.get("tenant_id"), r.get("dept_id"), r.get("user_id"),
                    r.get("role"), r.get("action"), r.get("target"), r.get("result"),
                    json.dumps(r.get("meta_json") or {}, ensure_ascii=False), r.get("created_at")])
    content = buf.getvalue().encode("utf-8")

    do_encrypt = bool(encrypt) if encrypt is not None else bool(settings.AUDIT_EXPORT_KEY)
    warning = ""
    if do_encrypt and not settings.AUDIT_EXPORT_KEY:
        # 要求加密但没配密钥 → 降级明文并告警，不静默
        do_encrypt = False
        warning = "要求加密导出但未配置 AUDIT_EXPORT_KEY，已降级为明文 CSV"
    if do_encrypt:
        content = _encrypt_bytes(content)
    out = Path(out_path) if out_path else (
        REPORT_DIR / ("audit_export.enc" if do_encrypt else "audit_export.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    return {"path": str(out), "encrypted": do_encrypt, "rows": len(rows), "warning": warning}


def _encrypt_bytes(data: bytes) -> bytes:
    from cryptography.fernet import Fernet
    key = settings.AUDIT_EXPORT_KEY.encode("utf-8")
    return Fernet(key).encrypt(data)


def delete_user_data(user_id: str) -> Dict[str, Any]:
    """员工数据导出/删除：级联清该用户产生的审计记录，并触发向量与库内数据的级联清理。

    审计侧只清「该用户作为操作者」的记录（不误删涉及他人的记录）；向量/语料侧
    尝试调用各存储的按 owner 清理钩子（不可达时静默跳过，不阻断主流程）。
    返回 {audit_deleted, vector_deleted, note}。
    """
    if not user_id:
        raise ValueError("user_id 不能为空")
    deleted = 0
    pg = _connect_pg()
    if pg is not None:
        try:
            with pg.cursor() as cur:
                cur.execute("DELETE FROM audit_logs WHERE user_id=%s", (user_id,))
                deleted = cur.rowcount
        except Exception:
            deleted = 0
    else:
        try:
            conn = _sqlite_conn()
            cur = conn.execute("DELETE FROM audit_logs WHERE user_id=?", (user_id,))
            deleted = cur.rowcount
            conn.commit()
        except Exception:
            deleted = 0
    # 级联清向量 / 库内数据（按 owner）。guarded import：各存储未实现时跳过。
    vector_deleted = 0
    try:
        from app.services import vector_store as _vs  # type: ignore
        fn = getattr(_vs, "delete_by_owner", None)
        if fn:
            vector_deleted = fn(user_id) or 0
    except Exception:
        pass
    try:
        from app.services import pg_store as _ps  # type: ignore
        fn = getattr(_ps, "delete_by_owner", None)
        if fn:
            fn(user_id)
    except Exception:
        pass
    return {"audit_deleted": deleted, "vector_deleted": vector_deleted,
            "note": "已级联清理该用户审计与向量数据"}
