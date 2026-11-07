"""M48 · 租户审批多级模板可视化编辑：PUT /tenants/{id}/approval-config 校验与持久化。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import approval_store, tenant_store

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")}


def _dept_admin():
    return {"Authorization": create_dev_token("u_da", "d_sales", "dept_admin", "t_demo")}


def _mk_tenant():
    r = client.post("/api/v1/tenants", json={"name": "Co_M48C"}, headers=_admin())
    assert r.status_code == 200, r.text
    return r.json()["data"]["tenant_id"]


def test_save_valid_stages_persists_and_resolves():
    tid = _mk_tenant()
    r = client.put(f"/api/v1/tenants/{tid}/approval-config",
                   json={"stages": [
                       {"name": "部门经理", "approver_role": "dept_admin"},
                       {"name": "平台管理员", "approver_role": "platform_admin"},
                   ], "allow_global_scope": True}, headers=_admin())
    assert r.status_code == 200, r.text
    cfg = r.json()["data"]["config"]["approval"]
    assert cfg["stages"][0]["name"] == "部门经理"
    assert cfg["allow_global_scope"] is True
    # resolve_stages 解析生效
    stages = approval_store.resolve_stages(tid)
    assert len(stages) == 2 and stages[1]["approver_role"] == "platform_admin"


def test_invalid_approver_role_rejected():
    tid = _mk_tenant()
    r = client.put(f"/api/v1/tenants/{tid}/approval-config",
                   json={"stages": [{"name": "X", "approver_role": "employee"}]}, headers=_admin())
    assert r.status_code == 400
    assert "非法" in r.json()["detail"]


def test_empty_stages_falls_back_to_single_default():
    tid = _mk_tenant()
    r = client.put(f"/api/v1/tenants/{tid}/approval-config",
                   json={"stages": [], "allow_global_scope": False}, headers=_admin())
    assert r.status_code == 200, r.text
    stages = approval_store.resolve_stages(tid)
    assert len(stages) == 1 and stages[0]["name"] == "默认审批"


def test_unknown_tenant_404():
    r = client.put("/api/v1/tenants/t_no_such/approval-config",
                   json={"stages": []}, headers=_admin())
    assert r.status_code == 404


def test_non_admin_forbidden():
    tid = _mk_tenant()
    r = client.put(f"/api/v1/tenants/{tid}/approval-config",
                   json={"stages": [{"name": "X", "approver_role": "dept_admin"}]},
                   headers=_dept_admin())
    assert r.status_code == 403
