"""M47 · pending_list「仅显示当前级我能审」过滤（与 decide 授权规则对称）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import approval_store, tenant_store

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_a47", "d_it", "platform_admin", "t_demo")}


def _mk_multistage_tenant():
    name = "Co_" + os.urandom(4).hex()
    r = client.post("/api/v1/tenants",
                    json={"name": name, "config": {"approval": {"stages": [
                        {"name": "部门经理", "approver_role": "dept_admin"},
                        {"name": "平台管理员", "approver_role": "platform_admin"},
                    ]}}},
                    headers=_admin())
    assert r.status_code == 200, r.text
    return r.json()["data"]["tenant_id"]


def _register(tid, dept="d_sales"):
    tok = {"Authorization": create_dev_token("u_emp47", dept, "employee", tid)}
    r = client.post("/api/v1/agents",
                    json={"name": "M47多级", "adapter_type": "llm_retrieval",
                          "description": "M47", "knowledge_scope": "department"}, headers=tok)
    assert r.status_code == 200, r.text
    return r.json()["data"]["approval_id"], tok


def test_can_decide_at_stage_helper():
    t = approval_store.create_ticket(
        "a47", "u_q", "d_sales", "t47",
        stages=[{"name": "L1", "approver_role": "dept_admin"},
                {"name": "L2", "approver_role": "platform_admin"}])
    try:
        # 当前级 = L1(dept_admin)
        assert approval_store._can_decide_at_stage(t, "dept_admin", "d_sales") is True
        assert approval_store._can_decide_at_stage(t, "platform_admin", "d_it") is True
        # dept_admin 跨部门不可审
        assert approval_store._can_decide_at_stage(t, "dept_admin", "d_other") is False
        # dept_admin 不能审要求 platform_admin 的级（末级）
        t["current_stage"] = 1
        assert approval_store._can_decide_at_stage(t, "dept_admin", "d_sales") is False
        assert approval_store._can_decide_at_stage(t, "platform_admin", "d_it") is True
    finally:
        approval_store._TICKETS.pop(t["approval_id"], None)


def test_pending_list_only_my_stage_narrows_for_dept_admin():
    tid = _mk_multistage_tenant()
    apr_id, _ = _register(tid)
    dadmin = {"Authorization": create_dev_token("u_da47", "d_sales", "dept_admin", tid)}
    padmin = {"Authorization": create_dev_token("u_pa47", "d_it", "platform_admin", tid)}
    # 当前级 = 部门经理(dept_admin)：dept_admin 看得到，platform_admin 也看得到（都能审）
    r_da = client.get("/api/v1/approval/pending?only_my_stage=true", headers=dadmin)
    assert r_da.status_code == 200, r_da.text
    ids_da = [x["approval_id"] for x in r_da.json()["data"]["tickets"]]
    assert apr_id in ids_da
    # 部门管理员跨部门应看不到
    dadmin_other = {"Authorization": create_dev_token("u_da47b", "d_other", "dept_admin", tid)}
    r_da2 = client.get("/api/v1/approval/pending?only_my_stage=true", headers=dadmin_other)
    assert apr_id not in [x["approval_id"] for x in r_da2.json()["data"]["tickets"]]
    # 平台管理员批第 1 级后，当前级 = 平台管理员(platform_admin)
    client.post(f"/api/v1/approval/{apr_id}/decide", json={"decision": "approve"}, headers=dadmin)
    # 现在 dept_admin 不应再出现（当前级要求 platform_admin）
    r_da3 = client.get("/api/v1/approval/pending?only_my_stage=true", headers=dadmin)
    assert apr_id not in [x["approval_id"] for x in r_da3.json()["data"]["tickets"]]
    # platform_admin 仍能看到并能终审
    r_pa = client.get("/api/v1/approval/pending?only_my_stage=true", headers=padmin)
    assert apr_id in [x["approval_id"] for x in r_pa.json()["data"]["tickets"]]


def test_pending_list_without_flag_returns_all_my_dept():
    tid = _mk_multistage_tenant()
    apr_id, _ = _register(tid)
    dadmin = {"Authorization": create_dev_token("u_da47c", "d_sales", "dept_admin", tid)}
    # 不带 only_my_stage：dept_admin 仍看到本部门全部（含当前级要 platform_admin 的）
    r = client.get("/api/v1/approval/pending", headers=dadmin)
    assert apr_id in [x["approval_id"] for x in r.json()["data"]["tickets"]]
