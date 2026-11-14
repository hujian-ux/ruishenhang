"""P1-11 审计哈希链（#306）契约测试。

隔离方式：把 EMBED_CACHE_PATH 指向 tmp_path（audit.db 由其派生），清 PG 连接探测缓存
与 SQLite 迁移标记，每个用例都是一张全新的库。conftest 默认 PG_PORT=1 不可达，
因此全量走 SQLite 兜底路径；PG 分支的 DDL 用假 cursor 单独验证。

覆盖：摘要规范、在线迁移、链式写入、开关降级、多租户、篡改检测、合法裁剪墓碑、
RBAC 与 HTTP 端点（含 routers/audit.py 原先缺失 HTTPException import 的 403 回归）。
"""
import csv
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.services import audit

ADMIN = CallerContext(userId="u_root", deptId="d_platform", role="platform_admin", tenantId="tA")
DEPT = CallerContext(userId="u_dept", deptId="d_a", role="dept_admin", tenantId="tA")
EMP = CallerContext(userId="u_emp", deptId="d_a", role="employee", tenantId="tA")
ADMIN_H = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin", "t_sys")}
DEPT_H = {"Authorization": create_dev_token("u_dept", "d_a", "dept_admin", "tA")}
EMP_H = {"Authorization": create_dev_token("u_emp", "d_a", "employee", "tA")}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EMBED_CACHE_PATH", str(tmp_path / "embed_cache.db"))
    monkeypatch.setattr(settings, "AUDIT_CHAIN_ENABLED", True)
    monkeypatch.setattr(settings, "AUDIT_CHAIN_KEY", "")
    monkeypatch.setattr(audit, "_pg_checked", False)
    monkeypatch.setattr(audit, "_pg_conn", None)
    monkeypatch.setattr(audit, "_sqlite_migrated", set())
    return tmp_path


def _conn():
    return audit._sqlite_conn()


def _raw(action="generate", created_at="2020-01-01T00:00:00+00:00", user="u_legacy"):
    """绕过服务层插一条 M13 时代的 10 列记录（无链字段）。"""
    c = _conn()
    c.execute(
        "INSERT INTO audit_logs (trace_id,tenant_id,dept_id,user_id,role,action,target,result,meta_json,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tr_legacy", "tA", "d_a", user, "employee", action, "x", "ok", "{}", created_at))
    c.commit()


def _seed(n=4, caller=None, action="generate"):
    c = caller or ADMIN
    for i in range(n):
        audit.record(action, c, target=f"t{i}", meta={"hits": i})
    return c


def _update(seq, **cols):
    c = _conn()
    sets = ",".join(f"{k}=?" for k in cols)
    c.execute(f"UPDATE audit_logs SET {sets} WHERE chain_seq=?", (*cols.values(), seq))
    c.commit()


# ── 摘要规范 ────────────────────────────────────────────────────────────────

def test_genesis_and_algo(env):
    assert audit.GENESIS == "0" * 64
    assert audit.chain_algo() == audit.CHAIN_ALGO_SHA


def test_compute_hash_deterministic(env):
    row = {"trace_id": "tr", "tenant_id": "tA", "dept_id": "d", "user_id": "u",
           "role": "employee", "action": "download", "target": "doc", "result": "ok",
           "meta_json": '{"hits":1}', "created_at": "2026-11-14T00:00:00.000000+00:00"}
    assert audit.compute_hash(1, audit.GENESIS, row) == audit.compute_hash(1, audit.GENESIS, row)
    assert len(audit.compute_hash(1, audit.GENESIS, row)) == 64


@pytest.mark.parametrize("field", ["trace_id", "tenant_id", "dept_id", "user_id", "role",
                                   "action", "target", "result", "created_at"])
def test_every_business_field_is_bound_into_hash(env, field):
    row = {"trace_id": "tr", "tenant_id": "tA", "dept_id": "d", "user_id": "u",
           "role": "employee", "action": "download", "target": "doc", "result": "ok",
           "meta_json": "{}", "created_at": "2026-11-14T00:00:00.000000+00:00"}
    a = audit.compute_hash(3, "p" * 64, row)
    row[field] = "CHANGED"
    assert audit.compute_hash(3, "p" * 64, row) != a


def test_seq_and_prev_hash_are_bound(env):
    row = {"trace_id": "tr", "tenant_id": "tA", "action": "download", "meta_json": "{}"}
    a = audit.compute_hash(1, audit.GENESIS, row)
    assert audit.compute_hash(2, audit.GENESIS, row) != a
    assert audit.compute_hash(1, "f" * 64, row) != a


def test_hmac_key_changes_digest_and_algo(env, monkeypatch):
    row = {"trace_id": "tr", "tenant_id": "tA", "action": "download", "meta_json": "{}"}
    plain = audit.compute_hash(1, audit.GENESIS, row)
    with monkeypatch.context() as m:
        m.setattr(settings, "AUDIT_CHAIN_KEY", "secret-key")
        assert audit.chain_algo() == audit.CHAIN_ALGO_HMAC
        assert audit.compute_hash(1, audit.GENESIS, row) != plain
    assert audit.compute_hash(1, audit.GENESIS, row) == plain


def test_canon_meta_string_and_dict_agree(env):
    assert audit._canon_meta('{"b":2,"a":1}') == audit._canon_meta({"a": 1, "b": 2})
    assert audit._canon_meta(None) == ""


def test_canon_ts_datetime_and_string_agree(env):
    dt = datetime(2026, 11, 14, 1, 2, 3, 123456, tzinfo=timezone.utc)
    text = dt.isoformat(timespec="microseconds")
    assert audit._canon_ts(dt) == audit._canon_ts(text) == text
    assert audit._canon_ts(None) == ""


def test_gap_hash_is_key_sensitive(env, monkeypatch):
    a = audit._gap_hash("tA", 1, 3, 3, "h", "retention_purge", "2026-11-14T00:00:00.000000+00:00")
    with monkeypatch.context() as m:
        m.setattr(settings, "AUDIT_CHAIN_KEY", "k")
        assert audit._gap_hash("tA", 1, 3, 3, "h", "retention_purge",
                               "2026-11-14T00:00:00.000000+00:00") != a


# ── 在线迁移 ────────────────────────────────────────────────────────────────

def test_sqlite_migration_upgrades_legacy_table(env):
    path = str(settings.EMBED_CACHE_PATH).replace("embed_cache.db", "audit.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, "
              "tenant_id TEXT, dept_id TEXT, user_id TEXT, role TEXT, action TEXT, "
              "target TEXT, result TEXT, meta_json TEXT, created_at TEXT)")
    c.commit(); c.close()
    audit._sqlite_migrated.clear()
    conn = _conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_logs)")}
    assert set(audit._CHAIN_COLS) <= cols
    for t in ("audit_chain_heads", "audit_chain_gaps"):
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                            (t,)).fetchone()[0] == 1


def test_sqlite_migration_idempotent(env):
    _conn()
    _conn()
    assert settings.EMBED_CACHE_PATH.replace("embed_cache.db", "audit.db") in audit._sqlite_migrated


def test_pg_ddl_creates_chain_tables_and_columns(env):
    """PG 不可达时用假 cursor 收集 DDL，确保链字段/链头/墓碑表都在迁移语句里。"""
    executed = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, *a): executed.append(sql)

    class Conn:
        def cursor(self): return Cur()

    audit._ensure_pg(Conn())
    text = "\n".join(executed)
    for col in audit._CHAIN_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in text
    assert "audit_chain_heads" in text and "audit_chain_gaps" in text


# ── 链式写入 ────────────────────────────────────────────────────────────────

def test_record_writes_chain_fields(env):
    _seed(1)
    r = audit.get_logs(ADMIN)[0]
    assert r["chain_seq"] == 1
    assert r["prev_hash"] == audit.GENESIS
    assert len(r["record_hash"]) == 64
    assert r["chain_algo"] == audit.CHAIN_ALGO_SHA


def test_seq_monotonic_and_prev_links(env):
    _seed(5)
    rows = sorted(audit.get_logs(ADMIN, limit=100), key=lambda x: x["chain_seq"])
    assert [r["chain_seq"] for r in rows] == [1, 2, 3, 4, 5]
    for i in range(1, len(rows)):
        assert rows[i]["prev_hash"] == rows[i - 1]["record_hash"]


def test_head_matches_tail(env):
    _seed(4)
    h = audit.chain_status()["heads"]
    assert len(h) == 1 and h[0]["last_seq"] == 4
    assert h[0]["last_hash"] == audit.get_logs(ADMIN)[0]["record_hash"]


def test_recompute_matches_stored_hash(env):
    _seed(3)
    for r in audit.get_logs(ADMIN):
        assert audit.compute_hash(int(r["chain_seq"]), r["prev_hash"], r) == r["record_hash"]


def test_disabled_switch_keeps_m13_shape(env, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_CHAIN_ENABLED", False)
    audit.record("generate", ADMIN, target="x")
    assert audit.get_logs(ADMIN)[0]["chain_seq"] is None
    assert audit.chain_status()["heads"] == []
    assert audit.verify_chain(ADMIN)["status"] in ("empty", "ok")


def test_multi_tenant_independent_chains(env):
    b = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="tB")
    _seed(3)
    _seed(2, caller=b)
    heads = {h["tenant_id"]: h["last_seq"] for h in audit.chain_status()["heads"]}
    assert heads == {"tA": 3, "tB": 2}
    assert audit.verify_chain(ADMIN, "tB")["verified"] == 2


def test_sanitize_still_blocks_sensitive_content(env):
    audit.record("download", ADMIN, target="doc", meta={"content": "机密", "title": "x"})
    r = audit.get_logs(ADMIN)[0]
    assert "content" not in r["meta_json"] and r["meta_json"]["title"] == "x"


def test_compliance_tag_is_bound_into_hash(env):
    audit.record("download", ADMIN, target="doc", compliance=["等保2.0-S3"])
    r = audit.get_logs(ADMIN)[0]
    assert r["meta_json"]["compliance"] == ["等保2.0-S3"]
    assert audit.compute_hash(r["chain_seq"], r["prev_hash"], r) == r["record_hash"]


def test_export_csv_carries_chain_columns(env, tmp_path):
    _seed(2)
    out = str(tmp_path / "audit_export.csv")
    audit.export_records(ADMIN, out_path=out, encrypt=False)
    with open(out, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0][-4:] == list(audit._CHAIN_COLS)
    assert rows[1][-4] == "2" and len(rows[1][-2]) == 64


# ── 验链 / 篡改检测 ──────────────────────────────────────────────────────────

def test_verify_ok_fresh_chain(env):
    _seed(4)
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["status"] == "ok"
    assert r["verified"] == 4 and r["broken_at"] is None and r["error"] == ""


def test_verify_empty_chain(env):
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["status"] == "empty" and r["verified"] == 0


@pytest.mark.parametrize("col,value", [
    ("target", "HACKED"),
    ("result", "denied"),
    ("meta_json", '{"hits":999}'),
    ("created_at", "2020-01-01T00:00:00.000000+00:00"),
    ("user_id", "u_other"),
])
def test_verify_detects_field_tamper(env, col, value):
    _seed(4)
    _update(2, **{col: value})
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is False and r["status"] == "tampered" and r["broken_at"] == 2
    assert "record_hash" in r["error"]


def test_verify_detects_record_hash_swap(env):
    _seed(3)
    _update(3, record_hash="a" * 64)
    r = audit.verify_chain(ADMIN)
    assert r["status"] == "tampered" and r["broken_at"] == 3


def test_verify_detects_prev_hash_swap(env):
    _seed(3)
    _update(3, prev_hash="b" * 64)
    r = audit.verify_chain(ADMIN)
    assert r["status"] == "tampered" and r["broken_at"] == 3
    assert "prev_hash" in r["error"]


def test_verify_detects_illegal_row_delete(env):
    _seed(5)
    c = _conn()
    c.execute("DELETE FROM audit_logs WHERE chain_seq=3"); c.commit()
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is False and r["status"] == "tampered"
    assert r["broken_at"] == 4


def test_verify_detects_head_tamper(env):
    _seed(3)
    c = _conn()
    c.execute("UPDATE audit_chain_heads SET last_hash=? WHERE tenant_id=?", ("c" * 64, "tA"))
    c.commit()
    r = audit.verify_chain(ADMIN)
    assert r["status"] == "tampered" and "链头" in r["error"]


def test_legacy_rows_are_counted_not_failed(env):
    _seed(2)
    _raw()
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["verified"] == 2 and r["legacy"] == 1


def test_chain_status_shape(env):
    _seed(2)
    s = audit.chain_status()
    assert s["enabled"] is True and s["algo"] == "sha256"
    assert s["key_configured"] is False and s["backend"] == "sqlite"
    assert s["genesis"] == audit.GENESIS and s["legacy"] == 0


def test_verify_limit_is_honored_without_false_tamper(env):
    _seed(6)
    r = audit.verify_chain(ADMIN, limit=2)
    assert r["verified"] == 2 and r["status"] == "partial"
    assert r["ok"] is True and r["truncated"] is True and "上限" in r["error"]


# ── 合法裁剪墓碑 ────────────────────────────────────────────────────────────

def _age(seqs):
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    c = _conn()
    for s in seqs:
        c.execute("UPDATE audit_logs SET created_at=? WHERE chain_seq=?", (old, s))
    c.commit()


def test_purge_writes_gap_and_verify_is_pruned(env):
    _seed(6)
    _age([1, 2])
    assert audit.purge_expired(180) == 2
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["status"] == "pruned" and r["verified"] == 4
    assert r["pruned"] == [{"from_seq": 1, "to_seq": 2, "reason": "retention_purge"}]
    assert audit.chain_status()["gaps"] == 1


def test_legal_hold_rows_survive_purge_and_chain_stays_ok(env):
    _seed(2)
    audit.record("approval", ADMIN, target="ap")
    # 只把将被清理的两条置旧：created_at 绑定在 record_hash 里，
    # 外部改写保留记录的时间戳本来就应被判篡改，不能为了造场景去改它。
    _age([1, 2])
    assert audit.purge_expired(180) == 2
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["status"] == "pruned" and r["verified"] == 1
    assert any(x["action"] == "approval" for x in audit.get_logs(ADMIN))


def test_delete_user_data_writes_gap(env):
    victim = CallerContext(userId="u_victim", deptId="d_a", role="employee", tenantId="tA")
    _seed(3)
    audit.record("generate", victim, target="v")
    audit.record("generate", ADMIN, target="after")
    res = audit.delete_user_data("u_victim")
    assert res["audit_deleted"] == 1
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["status"] == "pruned" and r["verified"] == 4
    assert any(p["reason"] == "data_subject_erasure" for p in r["pruned"])


def test_gap_hash_tamper_is_detected(env):
    _seed(5)
    _age([1, 2])
    audit.purge_expired(180)
    c = _conn()
    c.execute("UPDATE audit_chain_gaps SET gap_hash=?", ("0" * 64,))
    c.commit()
    r = audit.verify_chain(ADMIN)
    assert r["status"] == "tampered"


def test_gap_last_hash_tamper_is_detected(env):
    _seed(5)
    _age([1, 2])
    audit.purge_expired(180)
    c = _conn()
    c.execute("UPDATE audit_chain_gaps SET last_hash=?", ("d" * 64,))
    c.commit()
    r = audit.verify_chain(ADMIN)
    assert r["status"] == "tampered"


def test_illegal_delete_after_legal_purge_still_detected(env):
    _seed(7)
    _age([1, 2])
    audit.purge_expired(180)
    assert audit.verify_chain(ADMIN)["ok"] is True
    c = _conn()
    c.execute("DELETE FROM audit_logs WHERE chain_seq=5"); c.commit()
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is False and r["status"] == "tampered" and r["broken_at"] == 6


def test_all_records_deleted_with_tombstone_is_pruned(env):
    _seed(3)
    _age([1, 2, 3])
    assert audit.purge_expired(180) == 3
    r = audit.verify_chain(ADMIN)
    assert r["ok"] is True and r["status"] == "pruned" and r["verified"] == 0


def test_segments_groups_consecutive_sequences(env):
    victims = [("tA", 1, "h1"), ("tA", 2, "h2"), ("tA", 4, "h4"), ("tB", 1, "hb")]
    segs = audit._segments(victims)
    assert [(s["from"], s["to"], s["last"]) for s in segs["tA"]] == [(1, 2, "h2"), (4, 4, "h4")]
    assert len(segs["tB"]) == 1


# ── RBAC ────────────────────────────────────────────────────────────────────

def test_verify_employee_forbidden(env):
    _seed(2)
    with pytest.raises(HTTPException) as e:
        audit.verify_chain(EMP)
    assert e.value.status_code == 403


def test_verify_dept_admin_forced_to_own_tenant(env):
    _seed(2)
    b = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="tB")
    _seed(3, caller=b)
    r = audit.verify_chain(DEPT, tenant_id="tB")
    assert r["tenant_id"] == "tA" and r["verified"] == 2


def test_verify_platform_admin_all_tenants(env):
    _seed(2)
    b = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="tB")
    _seed(3, caller=b)
    r = audit.verify_chain(ADMIN, tenant_id="*")
    assert r["ok"] is True and len(r["tenants"]) >= 2
    assert r["verified"] >= 5


# ── HTTP 端点 ───────────────────────────────────────────────────────────────

def test_http_requires_auth(env):
    with TestClient(app) as cli:
        assert cli.get("/api/v1/audit/chain/status").status_code == 401
        assert cli.get("/api/v1/audit/chain/verify").status_code == 401


def test_http_employee_forbidden_on_chain_endpoints(env):
    with TestClient(app) as cli:
        assert cli.get("/api/v1/audit/chain/status", headers=EMP_H).status_code == 403
        assert cli.get("/api/v1/audit/chain/verify", headers=EMP_H).status_code == 403


def test_http_admin_status_and_verify(env):
    _seed(3)
    with TestClient(app) as cli:
        s = cli.get("/api/v1/audit/chain/status", headers=ADMIN_H)
        v = cli.get("/api/v1/audit/chain/verify?tenant_id=tA", headers=ADMIN_H)
    assert s.status_code == 200 and s.json()["data"]["algo"] == "sha256"
    assert v.status_code == 200 and v.json()["data"]["ok"] is True
    assert v.json()["data"]["status"] in ("ok", "pruned")
    assert v.json()["data"]["verified"] == 3


def test_http_verify_writes_its_own_audit_record(env):
    with TestClient(app) as cli:
        cli.get("/api/v1/audit/chain/verify", headers=ADMIN_H)
    assert any(r["action"] == "audit_chain_verify" for r in audit.get_logs(ADMIN, limit=50))


def test_http_purge_and_export_employee_403_not_500(env):
    """routers/audit.py 原先漏 import HTTPException，非管理员会 500；这里锁死 403。"""
    with TestClient(app) as cli:
        assert cli.post("/api/v1/audit/purge", headers=EMP_H).status_code == 403
        assert cli.post("/api/v1/audit/export", headers=EMP_H).status_code == 403


def test_http_logs_exposes_chain_fields(env):
    _seed(2)
    with TestClient(app) as cli:
        r = cli.get("/api/v1/audit/logs", headers=ADMIN_H)
    assert r.status_code == 200
    assert r.json()["data"]["logs"][0]["record_hash"]
