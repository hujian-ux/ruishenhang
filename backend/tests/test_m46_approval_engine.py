"""M46 · 多级审批引擎：租户模板、逐级推进、末级才发布、中途 reject、无模板回退单级、按级角色校验。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import approval_store, tenant_store

# 触发 lifespan：载回内置助手 / 公共库 / 115，保证 _AGENTS 非空。
client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")}


def _mk_tenant_with_stages(stages):
    name = "Co_" + os.urandom(4).hex()
    r = client.post("/api/v1/tenants",
                    json={"name": name, "config": {"approval": {"stages": stages}}},
                    headers=_admin())
    assert r.status_code == 200, r.text
    return r.json()["data"]["tenant_id"]


def _register(tenant_id, name="M46多级测试", dept="d_sales"):
    tok = {"Authorization": create_dev_token("u_emp", dept, "employee", tenant_id)}
    r = client.post("/api/v1/agents",
                    json={"name": name, "adapter_type": "llm_retrieval",
                          "description": "M46", "knowledge_scope": "department"},
                    headers=tok)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return d["agent_id"], d["approval_id"], tok


def test_no_tenant_template_falls_back_to_single_stage():
    stages = approval_store.resolve_stages("__no_such_tenant__")
    assert len(stages) == 1
    assert stages[0]["name"] == "默认审批"


def test_multistage_tenant_template_progresses_and_publishes_only_at_final():
    tid = _mk_tenant_with_stages([
        {"name": "部门经理", "approver_role": "dept_admin"},
        {"name": "平台管理员", "approver_role": "platform_admin"},
    ])
    agent_id, apr_id, emp = _register(tid)
    # 申请人查详情：两级、当前第 1 级、未终态
    det = client.get(f"/api/v1/approval/{apr_id}", headers=emp).json()["data"]
    assert det["total_stages"] == 2 and det["current_stage"] == 0 and not det["finished"]
    # 部门管理员批第 1 级 → 仍 pending，智能体仍不可调用（未发布）
    dadmin = {"Authorization": create_dev_token("u_da", "d_sales", "dept_admin", tid)}
    r1 = client.post(f"/api/v1/approval/{apr_id}/decide",
                     json={"decision": "approve", "comment": "部门通过"}, headers=dadmin)
    assert r1.status_code == 200, r1.text
    d1 = r1.json()["data"]
    assert d1["status"] == "pending" and d1["current_stage"] == 1
    assert d1["stage_name"] == "平台管理员" and d1["next_stage_name"] is None and not d1["finished"]
    # 未发布仍拦截
    inv = client.post("/api/v1/agent/invoke",
                     json={"agent_id": agent_id, "input": "hi"}, headers=emp)
    assert inv.status_code == 403
    # 平台管理员批第 2 级（末级）→ approved，智能体发布
    r2 = client.post(f"/api/v1/approval/{apr_id}/decide",
                     json={"decision": "approve", "comment": "平台通过"}, headers=_admin())
    assert r2.status_code == 200
    d2 = r2.json()["data"]
    assert d2["status"] == "approved" and d2["finished"] and d2["agent_status"] == "published"
    inv2 = client.post("/api/v1/agent/invoke",
                      json={"agent_id": agent_id, "input": "hi"}, headers=emp)
    assert inv2.status_code == 200


def test_reject_at_intermediate_stage_is_terminal():
    tid = _mk_tenant_with_stages([
        {"name": "部门经理", "approver_role": "dept_admin"},
        {"name": "平台管理员", "approver_role": "platform_admin"},
    ])
    agent_id, apr_id, emp = _register(tid)
    dadmin = {"Authorization": create_dev_token("u_da2", "d_sales", "dept_admin", tid)}
    r = client.post(f"/api/v1/approval/{apr_id}/decide",
                    json={"decision": "reject", "comment": "依据不足"}, headers=dadmin)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "rejected"
    assert r.json()["data"]["agent_status"] == "rejected"
    # 终态不可再批
    r2 = client.post(f"/api/v1/approval/{apr_id}/decide",
                     json={"decision": "approve"}, headers=_admin())
    assert r2.status_code == 409


def test_stage_role_enforced_store_level():
    # 第 1 级要求 platform_admin，dept_admin 越权应被拒
    t = approval_store.create_ticket(
        "a_x", "u_q", "d_sales", "t_x",
        stages=[{"name": "L1", "approver_role": "platform_admin"},
                {"name": "L2", "approver_role": "platform_admin"}])
    try:
        import pytest
        with pytest.raises(approval_store.ForbiddenDecider):
            approval_store.decide(t["approval_id"], "approve", "u_da", "dept_admin", "d_sales")
    finally:
        approval_store._TICKETS.pop(t["approval_id"], None)


def test_default_single_stage_still_publishes_on_one_approve():
    # 回归：无模板租户（默认单级）一次 approve 即 approved、agent published
    emp = {"Authorization": create_dev_token("u_def", "d_sales", "employee", "t_demo")}
    r = client.post("/api/v1/agents",
                    json={"name": "默认单级回归", "adapter_type": "llm_retrieval",
                          "description": "M46", "knowledge_scope": "department"}, headers=emp)
    apr_id = r.json()["data"]["approval_id"]
    d = client.post(f"/api/v1/approval/{apr_id}/decide",
                    json={"decision": "approve"}, headers=_admin())
    assert d.status_code == 200
    assert d.json()["data"]["status"] == "approved"
    assert d.json()["data"]["finished"] is True
