"""M49b · 复制租户审批模板到新租户：POST /tenants/{id}/copy-approval-config。

覆盖：复制 stages + allow_global_scope + notifiers；保留目标租户其它配置（brand）；
拒绝同源复制、未知目标租户、非法角色。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import tenant_store

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")}


def _tenant(cfg):
    r = client.post("/api/v1/tenants",
                    json={"name": "T49b_" + os.urandom(3).hex(), "config": cfg},
                    headers=_admin())
    assert r.status_code == 200, r.text
    return r.json()["data"]["tenant_id"]


SRC_APPROVAL = {
    "stages": [
        {"name": "部门经理", "approver_role": "dept_admin"},
        {"name": "平台管理员", "approver_role": "platform_admin"},
    ],
    "allow_global_scope": True,
    "notifiers": {"dept_admin": ["alice"], "platform_admin": ["boss"]},
}


def test_copy_template_copies_stages_and_notifiers():
    src = _tenant({"approval": SRC_APPROVAL})
    tgt = _tenant({})
    r = client.post(f"/api/v1/tenants/{src}/copy-approval-config",
                    json={"target_tenant_id": tgt}, headers=_admin())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["stages_copied"] == 2 and d["notifiers_copied"] == 2
    ap = tenant_store.get_config(tgt)["approval"]
    assert len(ap["stages"]) == 2
    assert ap["allow_global_scope"] is True
    assert ap["notifiers"]["dept_admin"] == ["alice"]
    assert ap["notifiers"]["platform_admin"] == ["boss"]


def test_copy_preserves_target_other_config():
    src = _tenant({"approval": SRC_APPROVAL})
    tgt = _tenant({"brand": {"name": "原品牌"}})
    r = client.post(f"/api/v1/tenants/{src}/copy-approval-config",
                    json={"target_tenant_id": tgt}, headers=_admin())
    assert r.status_code == 200
    cfg = tenant_store.get_config(tgt)
    assert cfg["brand"]["name"] == "原品牌", "目标租户其它配置不应被覆盖"
    assert "approval" in cfg


def test_copy_rejects_same_tenant():
    tid = _tenant({"approval": {"stages": [{"name": "x", "approver_role": "dept_admin"}]}})
    r = client.post(f"/api/v1/tenants/{tid}/copy-approval-config",
                    json={"target_tenant_id": tid}, headers=_admin())
    assert r.status_code == 400


def test_copy_404_unknown_target():
    src = _tenant({})
    r = client.post(f"/api/v1/tenants/{src}/copy-approval-config",
                    json={"target_tenant_id": "t_no_such"}, headers=_admin())
    assert r.status_code == 404


def test_copy_404_unknown_source():
    r = client.post("/api/v1/tenants/t_no_such/copy-approval-config",
                    json={"target_tenant_id": "t_no_such2"}, headers=_admin())
    assert r.status_code == 404


def test_copy_requires_admin():
    src = _tenant({"approval": SRC_APPROVAL})
    tgt = _tenant({})
    dept = {"Authorization": create_dev_token("u_da", "d_sales", "dept_admin", "t_demo")}
    r = client.post(f"/api/v1/tenants/{src}/copy-approval-config",
                    json={"target_tenant_id": tgt}, headers=dept)
    assert r.status_code == 403
