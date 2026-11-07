"""M29 审计留存与合规 单元测试（SQLite 兜底后端，隔离临时库）。"""
import os
import tempfile

import pytest

from app.core import config as config_mod
from app.core.config import settings
from app.models.caller import CallerContext
from app.services import audit as audit_svc


@pytest.fixture
def iso_db(tmp_path, monkeypatch):
    """把审计 SQLite 指向临时文件，保证测试隔离。"""
    embed = str(tmp_path / "embed_cache.db")
    monkeypatch.setattr(settings, "EMBED_CACHE_PATH", embed)
    # 重置连接缓存，强制用新路径
    audit_svc._pg_checked = False
    audit_svc._pg_conn = None
    return tmp_path


def _caller(role="platform_admin", uid="u_test"):
    return CallerContext(userId=uid, deptId="d_it", role=role, tenantId="t_demo")


def _insert_raw(action, created_at, uid="u_test"):
    conn = audit_svc._sqlite_conn()
    conn.execute(
        "INSERT INTO audit_logs (trace_id,tenant_id,dept_id,user_id,role,action,target,result,meta_json,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("t1", "t_demo", "d_it", uid, "employee", action, "x", "ok", "{}", created_at),
    )
    conn.commit()


def test_purge_expired_keeps_legal_hold(iso_db):
    # 旧的非保留动作（retrieve）应被删；旧的法定保留（approval）应保留；近期记录保留
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    _insert_raw("retrieve", old)
    _insert_raw("approval", old)   # 法定保留，永不过期
    _insert_raw("generate", recent)

    deleted = audit_svc.purge_expired(180)
    assert deleted == 1            # 只删了旧的 retrieve

    rows = audit_svc.get_logs(_caller())
    actions = {r["action"] for r in rows}
    assert "approval" in actions    # 法定保留仍在
    assert "retrieve" not in actions
    assert "generate" in actions


def test_record_compliance_tag(iso_db):
    audit_svc.record("download", _caller(), target="doc_1", result="ok",
                     compliance=["等保2.0-S3", "ISO27001-A.12.4"])
    rows = audit_svc.get_logs(_caller(), action="download")
    assert rows, "应写有一条 download 审计"
    assert rows[0]["meta_json"].get("compliance") == ["等保2.0-S3", "ISO27001-A.12.4"]


def test_export_records_csv(iso_db):
    audit_svc.record("generate", _caller(), target="a", compliance=["ISO27001-A.12.4"])
    audit_svc.record("retrieve", _caller(uid="u_other"), target="b")
    out = str(iso_db / "audit_export.csv")
    res = audit_svc.export_records(_caller(), out_path=out, encrypt=False)
    assert res["encrypted"] is False
    assert res["rows"] >= 2
    text = open(out, encoding="utf-8").read()
    assert "generate" in text and "retrieve" in text
    assert "ISO27001-A.12.4" in text


def test_export_records_encrypted(iso_db, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "AUDIT_EXPORT_KEY", key)
    audit_svc.record("download", _caller(), target="doc_1")
    out = str(iso_db / "audit_export.enc")
    res = audit_svc.export_records(_caller(), out_path=out, encrypt=True)
    assert res["encrypted"] is True
    # 加密文件不应是明文 CSV
    raw = open(out, "rb").read()
    assert b"download" not in raw
    # 用同一 key 能解密还原
    dec = Fernet(key.encode()).decrypt(raw).decode()
    assert "download" in dec


def test_delete_user_data_cascade(iso_db):
    _insert_raw("generate", "2020-01-01T00:00:00+00:00", uid="u_victim")
    _insert_raw("generate", "2020-01-01T00:00:00+00:00", uid="u_keep")
    res = audit_svc.delete_user_data("u_victim")
    assert res["audit_deleted"] == 1
    rows = audit_svc.get_logs(_caller())
    uids = {r["user_id"] for r in rows}
    assert "u_victim" not in uids
    assert "u_keep" in uids
