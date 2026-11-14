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

P1-11 审计哈希链：按租户分链，record() 是唯一写入点。
  chain_seq    : 该租户内单调递增的链序号（不依赖数据库自增，删除也不回退）
  prev_hash    : 上一条记录的 record_hash（首条为 GENESIS）
  record_hash  : HMAC-SHA256 / SHA-256 over 规范化载荷，含 seq + prev_hash + 全部业务字段
  audit_chain_heads : 持久链头（last_seq/last_hash），物理删除中间记录也不丢链尾
  audit_chain_gaps  : 合法裁剪墓碑（留存清理 / 数据主体删除），验链时据此识别合法缺口

诚实边界：这是 tamper-evident（可检测篡改），不是 tamper-proof（不能阻止改写）。
未配 AUDIT_CHAIN_KEY 时仅用 SHA-256，持有 DB 写权限者可重算整条链；配密钥后走
HMAC-SHA256，密钥不入库。要更强保证需外部只追加存储 / 定期锚定，不在本实现范围。
"""
import hashlib
import hmac
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

# ---- P1-11 哈希链 -----------------------------------------------------------

GENESIS = "0" * 64
CHAIN_ALGO_HMAC = "hmac-sha256"
CHAIN_ALGO_SHA = "sha256"
# 链头读写必须串行：SQLite 每次调用新建连接，进程内并发会同时读到同一 last_seq。
# PG 分支另用 SELECT ... FOR UPDATE 做跨进程互斥（见 _append_pg）。
_CHAIN_LOCK = threading.RLock()
_CHAIN_COLS = ("chain_seq", "prev_hash", "record_hash", "chain_algo")
# 已迁移过的 SQLite 库路径；测试用 tmp_path 换库时会自动重做迁移。
_sqlite_migrated: set[str] = set()


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
        # P1-11：老库在线迁移（IF NOT EXISTS 幂等，重启可反复跑）
        for ddl in (
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS chain_seq BIGINT",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS prev_hash TEXT",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS record_hash TEXT",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS chain_algo TEXT",
        ):
            cur.execute(ddl)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_chain_heads (
                tenant_id TEXT PRIMARY KEY,
                last_seq BIGINT NOT NULL,
                last_hash TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_chain_gaps (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                from_seq BIGINT NOT NULL,
                to_seq BIGINT NOT NULL,
                deleted_count INTEGER NOT NULL,
                last_hash TEXT NOT NULL,
                reason TEXT,
                gap_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_audit_gaps_tenant ON audit_chain_gaps (tenant_id, from_seq);
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
    if db_path not in _sqlite_migrated:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_logs)").fetchall()}
        for col, typ in (("chain_seq", "INTEGER"), ("prev_hash", "TEXT"),
                         ("record_hash", "TEXT"), ("chain_algo", "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE audit_logs ADD COLUMN {col} {typ}")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_chain_heads (
                tenant_id TEXT PRIMARY KEY,
                last_seq INTEGER NOT NULL,
                last_hash TEXT NOT NULL,
                updated_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_chain_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                from_seq INTEGER NOT NULL,
                to_seq INTEGER NOT NULL,
                deleted_count INTEGER NOT NULL,
                last_hash TEXT NOT NULL,
                reason TEXT,
                gap_hash TEXT NOT NULL,
                created_at TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_gaps_tenant "
                     "ON audit_chain_gaps (tenant_id, from_seq)")
        conn.commit()
        _sqlite_migrated.add(db_path)
    return conn


def _now_iso() -> str:
    # 规范化到微秒：与 _canon_ts 对 PG timestamptz 读回值的处理完全一致，
    # 保证「写入时算的 hash」和「验链时重算的 hash」用的是同一个字符串。
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canon_ts(v: Any) -> str:
    """把 PG datetime / SQLite 字符串 / 旧格式值统一成规范化时间戳字符串。"""
    if v is None:
        return ""
    if hasattr(v, "astimezone"):
        return v.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return str(v)


def _canon(v: Any) -> str:
    """载荷字段规范化：None→空串，dict/list→紧凑 JSON，避免序列化分叉。"""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(v)


def _canon_meta(v: Any) -> str:
    """meta_json 可能是「写入时的字符串」或「PG JSONB 读回的 dict」，两边必须同构。"""
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return v
    return _canon(v)


def compute_hash(seq: int, prev_hash: str, row: Dict[str, Any]) -> str:
    """单条记录摘要。canonical 含 seq + prev_hash + 全部业务字段（含 created_at）。"""
    parts = [str(seq), prev_hash or GENESIS,
             _canon(row.get("trace_id")), _canon(row.get("tenant_id")),
             _canon(row.get("dept_id")), _canon(row.get("user_id")),
             _canon(row.get("role")), _canon(row.get("action")),
             _canon(row.get("target")), _canon(row.get("result")),
             _canon_meta(row.get("meta_json")), _canon_ts(row.get("created_at"))]
    raw = "|".join(parts).encode("utf-8")
    key = (getattr(settings, "AUDIT_CHAIN_KEY", "") or "").encode("utf-8")
    if key:
        return hmac.new(key, raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def chain_algo() -> str:
    return CHAIN_ALGO_HMAC if (getattr(settings, "AUDIT_CHAIN_KEY", "") or "") else CHAIN_ALGO_SHA


def _gap_hash(tenant_id: str, from_seq: int, to_seq: int, count: int,
              last_hash: str, reason: str, created_at: str) -> str:
    raw = "|".join([_canon(tenant_id), str(from_seq), str(to_seq), str(count),
                    last_hash or GENESIS, _canon(reason), _canon_ts(created_at)]).encode("utf-8")
    key = (getattr(settings, "AUDIT_CHAIN_KEY", "") or "").encode("utf-8")
    if key:
        return hmac.new(key, raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


# ---- 写入（P1-11：链式写入收敛在 record 单点） -------------------------------

_INSERT_COLS = ("trace_id", "tenant_id", "dept_id", "user_id", "role",
                "action", "target", "result", "meta_json")


def _append_pg(pg, payload: Dict[str, Any]) -> None:
    """PG 单事务追加：锁链头 → 算 hash → 写审计 → 推进链头。"""
    enabled = bool(getattr(settings, "AUDIT_CHAIN_ENABLED", True))
    tenant = payload["tenant_id"]
    with pg.cursor() as cur:
        cur.execute("BEGIN")
        try:
            seq, prev, ts = 0, GENESIS, ""
            digest, algo = "", ""
            if enabled:
                cur.execute("SELECT last_seq, last_hash FROM audit_chain_heads "
                            "WHERE tenant_id=%s FOR UPDATE", (tenant,))
                head = cur.fetchone()
                if head:
                    seq, prev = int(head[0]) + 1, head[1] or GENESIS
                else:
                    cur.execute("INSERT INTO audit_chain_heads "
                                "(tenant_id,last_seq,last_hash,updated_at) VALUES (%s,0,%s,now()) "
                                "ON CONFLICT (tenant_id) DO NOTHING", (tenant, GENESIS))
                    seq, prev = 1, GENESIS
                ts = _now_iso()
                digest = compute_hash(seq, prev, {**payload, "created_at": ts})
                algo = chain_algo()
            vals = [payload[c] for c in _INSERT_COLS]
            if enabled:
                cur.execute(
                    """INSERT INTO audit_logs
                       (trace_id,tenant_id,dept_id,user_id,role,action,target,result,meta_json,
                        created_at,chain_seq,prev_hash,record_hash,chain_algo)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (*vals, ts, seq, prev, digest, algo))
                cur.execute("UPDATE audit_chain_heads SET last_seq=%s,last_hash=%s,updated_at=now() "
                            "WHERE tenant_id=%s", (seq, digest, tenant))
            else:
                cur.execute(
                    """INSERT INTO audit_logs
                       (trace_id,tenant_id,dept_id,user_id,role,action,target,result,meta_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", tuple(vals))
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def _append_sqlite(conn, payload: Dict[str, Any]) -> None:
    """SQLite 兜底追加：调用方持 _CHAIN_LOCK，单进程内链序号严格单调。"""
    enabled = bool(getattr(settings, "AUDIT_CHAIN_ENABLED", True))
    tenant = payload["tenant_id"]
    vals = [payload[c] for c in _INSERT_COLS]
    if not enabled:
        conn.execute(
            """INSERT INTO audit_logs
               (trace_id,tenant_id,dept_id,user_id,role,action,target,result,meta_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", (*vals, _now_iso()))
        conn.commit()
        return
    head = conn.execute("SELECT last_seq,last_hash FROM audit_chain_heads WHERE tenant_id=?",
                        (tenant,)).fetchone()
    seq, prev = (int(head[0]) + 1, head[1] or GENESIS) if head else (1, GENESIS)
    ts = _now_iso()
    digest = compute_hash(seq, prev, {**payload, "created_at": ts})
    conn.execute(
        """INSERT INTO audit_logs
           (trace_id,tenant_id,dept_id,user_id,role,action,target,result,meta_json,
            created_at,chain_seq,prev_hash,record_hash,chain_algo)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (*vals, ts, seq, prev, digest, chain_algo()))
    conn.execute(
        """INSERT INTO audit_chain_heads (tenant_id,last_seq,last_hash,updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(tenant_id) DO UPDATE SET
             last_seq=excluded.last_seq, last_hash=excluded.last_hash,
             updated_at=excluded.updated_at""", (tenant, seq, digest, ts))
    conn.commit()


def record(action: str, caller: CallerContext, target: str = "", result: str = "ok",
          meta: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None,
          compliance: Optional[list[str]] = None) -> str:
    """记录一条审计事件，返回 trace_id。meta 会被 JSON 化且脱敏（不存原文正文）。

    compliance：合规条款标签（如 ["等保2.0-S3","ISO27001-A.12.4"]），随 meta 存，供合规映射聚合。
    P1-11：开启 AUDIT_CHAIN_ENABLED（默认开）时，同一事务内写入链字段并推进链头；
    关闭时保持 M13 原写入形态。PG 失败仍降级 SQLite，业务侧无感。
    """
    if compliance:
        m = dict(meta or {})
        m["compliance"] = list(compliance)
        meta = m
    if trace_id is None:
        trace_id = getattr(_local, "trace_id", None) or uuid.uuid4().hex[:16]
    payload = {
        "trace_id": trace_id, "tenant_id": caller.tenantId, "dept_id": caller.deptId,
        "user_id": caller.userId, "role": caller.role, "action": action,
        "target": target, "result": result,
        "meta_json": json.dumps(_sanitize(meta or {}), ensure_ascii=False),
    }
    with _CHAIN_LOCK:
        pg = _connect_pg()
        if pg is not None:
            try:
                _append_pg(pg, payload)
                return trace_id
            except Exception:
                pass  # 落到 SQLite 兜底
        try:
            _append_sqlite(_sqlite_conn(), payload)
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
                            target, result, meta_json, created_at,
                            chain_seq, prev_hash, record_hash, chain_algo
                     FROM audit_logs WHERE 1=1"""
            params: List[Any] = []
            if caller.role == "dept_admin":
                sql += " AND dept_id=%s"; params.append(caller.deptId)
                sql += " AND tenant_id=%s"; params.append(caller.tenantId)
            elif caller.role == "employee":
                sql += " AND user_id=%s"; params.append(caller.userId)
                sql += " AND tenant_id=%s"; params.append(caller.tenantId)
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
                            target, result, meta_json, created_at,
                            chain_seq, prev_hash, record_hash, chain_algo
                     FROM audit_logs WHERE 1=1"""
            params = []
            if caller.role == "dept_admin":
                sql += " AND dept_id=?"; params.append(caller.deptId)
                sql += " AND tenant_id=?"; params.append(caller.tenantId)
            elif caller.role == "employee":
                sql += " AND user_id=?"; params.append(caller.userId)
                sql += " AND tenant_id=?"; params.append(caller.tenantId)
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
            "action", "target", "result", "meta_json", "created_at",
            "chain_seq", "prev_hash", "record_hash", "chain_algo"]
    d = dict(zip(cols, r))
    if isinstance(d.get("meta_json"), str):
        try:
            d["meta_json"] = json.loads(d["meta_json"])
        except Exception:
            pass
    return d


# ── P1-11 审计哈希链：合法裁剪墓碑 / 验链 / 链状态 ──────────────────────────

_CHAIN_ROW_COLS = ["trace_id", "tenant_id", "dept_id", "user_id", "role",
                   "action", "target", "result", "meta_json", "created_at",
                   "chain_seq", "prev_hash", "record_hash", "chain_algo"]


def _ph(backend: str) -> str:
    return "%s" if backend == "pg" else "?"


def _open() -> tuple[str, Any, Any]:
    """返回 (backend, conn, cursor)。PG 优先，失败走 SQLite。"""
    pg = _connect_pg()
    if pg is not None:
        return "pg", pg, pg.cursor()
    conn = _sqlite_conn()
    return "sqlite", conn, conn.cursor()


def _rows(cur, sql: str, args=()) -> list:
    cur.execute(sql, args)
    return list(cur.fetchall() or [])


def _chain_rows(cur, backend: str, tenant_id: str, limit: int) -> List[Dict[str, Any]]:
    p = _ph(backend)
    rs = _rows(cur,
               "SELECT trace_id,tenant_id,dept_id,user_id,role,action,target,result,"
               "meta_json,created_at,chain_seq,prev_hash,record_hash,chain_algo "
               f"FROM audit_logs WHERE tenant_id={p} AND chain_seq IS NOT NULL "
               f"ORDER BY chain_seq ASC LIMIT {p}", (tenant_id, limit))
    return [dict(zip(_CHAIN_ROW_COLS, r)) for r in rs]


def _heads(cur, backend: str, tenant_id: str = "") -> List[Dict[str, Any]]:
    p = _ph(backend)
    sql = "SELECT tenant_id,last_seq,last_hash,updated_at FROM audit_chain_heads"
    args: tuple = ()
    if tenant_id:
        sql += f" WHERE tenant_id={p}"
        args = (tenant_id,)
    sql += " ORDER BY tenant_id"
    return [{"tenant_id": r[0], "last_seq": int(r[1]), "last_hash": r[2],
             "updated_at": _canon_ts(r[3])} for r in _rows(cur, sql, args)]


def _gaps(cur, backend: str, tenant_id: str) -> List[Dict[str, Any]]:
    p = _ph(backend)
    rs = _rows(cur,
               "SELECT tenant_id,from_seq,to_seq,deleted_count,last_hash,reason,gap_hash,created_at "
               f"FROM audit_chain_gaps WHERE tenant_id={p} ORDER BY from_seq", (tenant_id,))
    return [{"tenant_id": r[0], "from_seq": int(r[1]), "to_seq": int(r[2]),
             "deleted_count": int(r[3]), "last_hash": r[4], "reason": r[5] or "",
             "gap_hash": r[6] or "", "created_at": _canon_ts(r[7])} for r in rs]


def _gap_valid(g: Dict[str, Any]) -> bool:
    want = _gap_hash(g["tenant_id"], g["from_seq"], g["to_seq"],
                     g["deleted_count"], g["last_hash"], g["reason"], g["created_at"])
    return hmac.compare_digest(str(g["gap_hash"] or ""), want)


def _cover_gaps(gaps: List[Dict[str, Any]], lo: int, hi: int,
                expected_last: str) -> Optional[List[Dict[str, Any]]]:
    """合法裁剪必须连续覆盖 [lo, hi]，且最后覆盖 hi 的墓碑持有衔接哈希。"""
    used: List[Dict[str, Any]] = []
    cursor = lo
    for g in sorted(gaps, key=lambda x: x["from_seq"]):
        if not _gap_valid(g) or g["from_seq"] > cursor or g["to_seq"] < cursor:
            continue
        used.append(g)
        cursor = g["to_seq"] + 1
        if cursor > hi:
            break
    if cursor <= hi:
        return None
    tail = [g for g in used if g["from_seq"] <= hi <= g["to_seq"]]
    if not tail or not hmac.compare_digest(str(tail[-1]["last_hash"] or ""), expected_last or ""):
        return None
    return used


def _segments(victims: List[tuple]) -> Dict[str, list]:
    """按租户 + 连续 seq 把将被删除的记录切成墓碑段。"""
    by_tenant: Dict[str, list] = {}
    for tenant, seq, digest in victims:
        by_tenant.setdefault(tenant, []).append((int(seq), digest or ""))
    out: Dict[str, list] = {}
    for tenant, pairs in by_tenant.items():
        segs: list = []
        cur_seg = None
        for seq, digest in sorted(pairs):
            if cur_seg and seq == cur_seg["to"] + 1:
                cur_seg.update({"to": seq, "count": cur_seg["count"] + 1, "last": digest})
            else:
                cur_seg = {"from": seq, "to": seq, "count": 1, "last": digest}
                segs.append(cur_seg)
        out[tenant] = segs
    return out


def _snapshot(cur, backend: str, where_sql: str, args: list) -> List[tuple]:
    """删除前取回将被删的入链记录 (tenant_id, chain_seq, record_hash)。"""
    p = _ph(backend)
    rs = _rows(cur,
               f"SELECT tenant_id,chain_seq,record_hash FROM audit_logs WHERE {where_sql} "
               "AND chain_seq IS NOT NULL ORDER BY tenant_id,chain_seq", args)
    return [(str(r[0]), int(r[1]), str(r[2] or "")) for r in rs]


def _write_gaps(cur, backend: str, victims: List[tuple], reason: str) -> int:
    """为一次合法删除写墓碑（留存清理 / 数据主体删除），删除前调用。"""
    p = _ph(backend)
    n = 0
    for tenant, segs in _segments(victims).items():
        for seg in segs:
            ts = _now_iso()
            gh = _gap_hash(tenant, seg["from"], seg["to"], seg["count"],
                           seg["last"], reason, ts)
            cur.execute(
                "INSERT INTO audit_chain_gaps "
                "(tenant_id,from_seq,to_seq,deleted_count,last_hash,reason,gap_hash,created_at) "
                f"VALUES ({','.join([p] * 8)})",
                (tenant, seg["from"], seg["to"], seg["count"], seg["last"], reason, gh, ts))
            n += 1
    return n


def _verify_tenant(cur, backend: str, tenant_id: str, limit: int) -> Dict[str, Any]:
    base = {"tenant_id": tenant_id, "verified": 0, "legacy": 0, "pruned": [],
            "broken_at": None, "error": "", "head": None, "status": "empty",
            "ok": True, "truncated": False, "algo": chain_algo()}
    try:
        rows = _chain_rows(cur, backend, tenant_id, limit)
        hs = _heads(cur, backend, tenant_id)
        head = hs[0] if hs else None
        gaps = _gaps(cur, backend, tenant_id)
        p = _ph(backend)
        legacy = _rows(cur, f"SELECT COUNT(*) FROM audit_logs WHERE tenant_id={p} "
                            "AND chain_seq IS NULL", (tenant_id,))
        base["legacy"] = int(legacy[0][0]) if legacy else 0
    except Exception as exc:
        base.update({"status": "error", "ok": False, "error": f"验链读取失败：{exc}"})
        return base
    base["head"] = head
    # 单次上限截断了链尾时，不能把「链头领先」误判成篡改；如实标 partial
    truncated = len(rows) >= limit and bool(head and head["last_seq"] > int(rows[-1]["chain_seq"]))
    if truncated:
        base["truncated"] = True
        base["error"] = f"单次校验上限 {limit} 条，链尾未完整校验"
    if not rows:
        if head and head["last_seq"] > 0:
            # 该租户记录被全部删除：必须有覆盖 1..last_seq 的合法墓碑
            covered = _cover_gaps(gaps, 1, head["last_seq"], head["last_hash"])
            if covered:
                base["status"] = "pruned"
                base["pruned"] = [{"from_seq": g["from_seq"], "to_seq": g["to_seq"],
                                   "reason": g["reason"]} for g in covered]
            else:
                base.update({"status": "tampered", "ok": False,
                             "broken_at": head["last_seq"],
                             "error": "链头显示曾有记录，但现存链为空且无合法裁剪墓碑"})
        return base

    expected_prev, prev_seq, verified, pruned = GENESIS, 0, 0, []
    for r in rows:
        seq = int(r["chain_seq"])
        if not hmac.compare_digest(str(r["prev_hash"] or ""), expected_prev):
            covered = _cover_gaps(gaps, prev_seq + 1, seq - 1, str(r["prev_hash"] or ""))
            if not covered:
                base.update({"status": "tampered", "ok": False, "broken_at": seq,
                             "verified": verified, "pruned": pruned,
                             "error": f"seq={seq} 的 prev_hash 不衔接，且无合法裁剪墓碑"})
                return base
            pruned.extend({"from_seq": g["from_seq"], "to_seq": g["to_seq"],
                           "reason": g["reason"]} for g in covered)
        want = compute_hash(seq, str(r["prev_hash"] or ""), r)
        if not hmac.compare_digest(want, str(r["record_hash"] or "")):
            base.update({"status": "tampered", "ok": False, "broken_at": seq,
                         "verified": verified, "pruned": pruned,
                         "error": f"seq={seq} 的 record_hash 重算不一致（记录字段被改写）"})
            return base
        expected_prev, prev_seq, verified = str(r["record_hash"]), seq, verified + 1

    if head:
        if head["last_seq"] > prev_seq:
            if truncated:
                base["status"] = "partial"       # 上限截断，不做链尾结论
            else:
                covered = _cover_gaps(gaps, prev_seq + 1, head["last_seq"], head["last_hash"])
                if not covered:
                    base.update({"status": "tampered", "ok": False,
                                 "broken_at": head["last_seq"], "verified": verified,
                                 "pruned": pruned,
                                 "error": "链头领先于现存链尾，且无合法裁剪墓碑"})
                    return base
                pruned.extend({"from_seq": g["from_seq"], "to_seq": g["to_seq"],
                               "reason": g["reason"]} for g in covered)
        elif not hmac.compare_digest(str(head["last_hash"] or ""), expected_prev):
            base.update({"status": "tampered", "ok": False, "broken_at": prev_seq,
                         "verified": verified, "pruned": pruned,
                         "error": "链头哈希与现存链尾不一致（链头或记录被改写）"})
            return base
    final = "partial" if truncated else ("pruned" if pruned else "ok")
    base.update({"status": final, "verified": verified, "pruned": pruned})
    return base


def verify_chain(caller: CallerContext, tenant_id: str = "",
                 limit: Optional[int] = None) -> Dict[str, Any]:
    """校验审计哈希链完整性（不返回任何审计明细，只给结论/序号/哈希）。

    权限：employee 禁止；dept_admin 强制限定本租户；platform_admin 可指定租户，
    tenant_id="*" 时校验全部租户并汇总。
    """
    from fastapi import HTTPException
    if caller.role == "employee":
        raise HTTPException(status_code=403, detail="forbidden: 仅管理员可校验审计链完整性")
    cap = int(limit or getattr(settings, "AUDIT_CHAIN_VERIFY_MAX", 20000))
    cap = max(1, min(cap, 200000))
    backend, _conn, cur = _open()
    try:
        if caller.role != "platform_admin":
            tid = caller.tenantId          # dept_admin 忽略传参，防越权
        else:
            tid = tenant_id or caller.tenantId
        if tid == "*" and caller.role == "platform_admin":
            tenants = sorted({h["tenant_id"] for h in _heads(cur, backend)} | {caller.tenantId})
            results = [_verify_tenant(cur, backend, t, cap) for t in tenants]
            return {
                "ok": all(r["ok"] for r in results),
                "status": "tampered" if any(r["status"] == "tampered" for r in results)
                          else ("pruned" if any(r["status"] == "pruned" for r in results)
                                else ("partial" if any(r["status"] == "partial" for r in results)
                                      else "ok")),
                "algo": chain_algo(), "backend": backend,
                "verified": sum(r["verified"] for r in results),
                "legacy": sum(r["legacy"] for r in results),
                "truncated": any(r.get("truncated") for r in results),
                "pruned": [p for r in results for p in r["pruned"]],
                "tenants": results, "broken_at": None, "error": "",
            }
        r = _verify_tenant(cur, backend, tid, cap)
        r.update({"backend": backend, "tenants": []})
        return r
    finally:
        try:
            cur.close()
        except Exception:
            pass


def chain_status(caller: Optional[CallerContext] = None) -> Dict[str, Any]:
    """链配置与链头摘要（不含审计明细，供管理台展示）。"""
    backend, _conn, cur = _open()
    try:
        heads = _heads(cur, backend)
        p = _ph(backend)
        legacy = _rows(cur, "SELECT COUNT(*) FROM audit_logs WHERE chain_seq IS NULL")
        gaps = _rows(cur, "SELECT COUNT(*) FROM audit_chain_gaps")
        return {
            "enabled": bool(getattr(settings, "AUDIT_CHAIN_ENABLED", True)),
            "algo": chain_algo(),
            "key_configured": bool(getattr(settings, "AUDIT_CHAIN_KEY", "")),
            "backend": backend,
            "verify_max": int(getattr(settings, "AUDIT_CHAIN_VERIFY_MAX", 20000)),
            "heads": [{"tenant_id": h["tenant_id"], "last_seq": h["last_seq"],
                       "last_hash": h["last_hash"], "updated_at": h["updated_at"]}
                      for h in heads],
            "legacy": int(legacy[0][0]) if legacy else 0,
            "gaps": int(gaps[0][0]) if gaps else 0,
            "genesis": GENESIS,
            "note": "哈希链为 tamper-evident（可检测篡改）；配置 AUDIT_CHAIN_KEY 后走 HMAC，密钥不入库。",
        }
    finally:
        try:
            cur.close()
        except Exception:
            pass


# ── P1-29 审计留存与合规：留存 TTL / 只读加密导出 / 等保-ISO 映射 / 级联删除 ──

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
                where = f"created_at < now() - make_interval(days => %s){hold_sql}"
                args = (days, *params_hold)
                cur.execute("BEGIN")
                try:
                    # P1-11：物理删除前写合法裁剪墓碑，否则哈希链无法区分「合规清理」与「篡改」
                    victims = _snapshot(cur, "pg", where, list(args))
                    _write_gaps(cur, "pg", victims, "retention_purge")
                    cur.execute(f"DELETE FROM audit_logs WHERE {where}", args)
                    deleted = cur.rowcount
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise
        except Exception:
            return 0
        return deleted
    # SQLite 兜底：created_at 为 UTC ISO 字符串，字典序即时间序
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = _sqlite_conn()
        hold_lite = (" AND action NOT IN (%s)" % ",".join(["?"] * len(hold))) if hold else ""
        where = "created_at < ?" + hold_lite
        args: list = [cutoff] + list(params_hold)
        cur = conn.cursor()
        victims = _snapshot(cur, "sqlite", where, args)
        _write_gaps(cur, "sqlite", victims, "retention_purge")
        cur.execute(f"DELETE FROM audit_logs WHERE {where}", args)
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
    # P1-11：导出带上链字段，合规方可离线重算 record_hash 复验（不依赖平台接口）
    w.writerow(["trace_id", "tenant_id", "dept_id", "user_id", "role",
                "action", "target", "result", "meta_json", "created_at",
                "chain_seq", "prev_hash", "record_hash", "chain_algo"])
    for r in rows:
        w.writerow([r.get("trace_id"), r.get("tenant_id"), r.get("dept_id"), r.get("user_id"),
                    r.get("role"), r.get("action"), r.get("target"), r.get("result"),
                    json.dumps(r.get("meta_json") or {}, ensure_ascii=False), r.get("created_at"),
                    r.get("chain_seq"), r.get("prev_hash"), r.get("record_hash"),
                    r.get("chain_algo")])
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
                cur.execute("BEGIN")
                try:
                    victims = _snapshot(cur, "pg", "user_id=%s", [user_id])
                    _write_gaps(cur, "pg", victims, "data_subject_erasure")
                    cur.execute("DELETE FROM audit_logs WHERE user_id=%s", (user_id,))
                    deleted = cur.rowcount
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise
        except Exception:
            deleted = 0
    else:
        try:
            conn = _sqlite_conn()
            cur = conn.cursor()
            victims = _snapshot(cur, "sqlite", "user_id=?", [user_id])
            _write_gaps(cur, "sqlite", victims, "data_subject_erasure")
            cur.execute("DELETE FROM audit_logs WHERE user_id=?", (user_id,))
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
