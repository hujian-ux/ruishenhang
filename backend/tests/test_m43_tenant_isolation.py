"""M43 · 多租户隔离 + 租户开通回归。

这是把平台从「单企业·多部门」升级为「多企业 SaaS」的核心安全特性，必须有测试：

- 智能体目录 / 调用 / 审批单 / 会话按 tenant_id 隔离：跨租户不可见、不可调用
  （与「无权=不存在」统一 404）。
- 平台级共享资产（115 业务包 / 28 公共库 / 内置助手，tenant_id 留空）对所有租户可见，开通时不克隆。
- 租户注册表：platform_admin 开通/激活，非管理员 403。
- 租户级 LLM 模型覆盖生效。
"""
from fastapi.testclient import TestClient

import pytest

from app.core import security
from app.main import app
from app.models.caller import CallerContext
from app.services import agent_store, approval_store, tenant_store
from app.services.model_gateway import resolve_model

c = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _trigger_startup():
    # 触发 lifespan（载回内置助手 / 公共库 / 115）。TestClient 直接调用不跑 lifespan，
    # 必须用 with 上下文；模块级一次即可，_AGENTS 等模块全局在 with 退出后仍保留。
    with c:
        yield


def _ctx(role, tenant="t_demo", userId="u1", deptId="d1"):
    return security.encode_caller_context(
        CallerContext(userId=userId, deptId=deptId, role=role, tenantId=tenant)
    )


# ── 智能体目录租户隔离 ──
def test_platform_asset_visible_to_all_tenants():
    # 内置助手 / 公共库 tenant_id 留空 → 任意租户员工可见（同一份共享资产）
    a = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_a")
    b = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="t_b")
    ids_a = {x["agent_id"] for x in agent_store.list_all(a) if x.get("tenant_id") in (None, "")}
    ids_b = {x["agent_id"] for x in agent_store.list_all(b) if x.get("tenant_id") in (None, "")}
    assert ids_a and ids_a == ids_b  # 两租户看到同一份平台共享资产


def test_tenant_private_agent_hidden_from_other_tenant():
    tid_a = "t_iso_a"
    agent_store.save({
        "agent_id": "ag_private_iso", "name": "租户A私有", "adapter_type": "llm",
        "knowledge_scope": "global", "model_id": None, "registered_by": "u_a",
        "dept_id": "d_a", "tenant_id": tid_a, "status": "published",
        "system_prompt": "",
    })
    try:
        same = CallerContext(userId="u_a2", deptId="d_a", role="employee", tenantId=tid_a)
        other = CallerContext(userId="u_b", deptId="d_b", role="dept_admin", tenantId="t_iso_b")
        admin = CallerContext(userId="u_p", deptId="d_p", role="platform_admin", tenantId="t_platform")
        assert agent_store._visible(agent_store.get("ag_private_iso"), same)
        assert not agent_store._visible(agent_store.get("ag_private_iso"), other)  # 跨租户不可见
        assert agent_store._visible(agent_store.get("ag_private_iso"), admin)     # 平台管理员可见
        rows_other = agent_store.list_all(other)
        assert "ag_private_iso" not in {x["agent_id"] for x in rows_other}
    finally:
        agent_store._AGENTS.pop("ag_private_iso", None)


def test_invoke_cross_tenant_returns_404():
    agent_store.save({
        "agent_id": "ag_inv_iso", "name": "租户A私有可调用", "adapter_type": "llm",
        "knowledge_scope": "owner", "model_id": None, "registered_by": "u_a",
        "dept_id": "d_a", "tenant_id": "t_iso_a", "status": "published",
        "system_prompt": "",
    })
    try:
        # 跨租户调用 → 404（与「无权=不存在」统一口径）
        r = c.post("/api/v1/agent/invoke", headers={"X-Caller-Context": _ctx("employee", tenant="t_iso_b")},
                   json={"agent_id": "ag_inv_iso", "input": "hi"})
        assert r.status_code == 404
        # 同租户调用 → 不因租户被拒（可能走 stub，但绝不是 404）
        r2 = c.post("/api/v1/agent/invoke", headers={"X-Caller-Context": _ctx("employee", tenant="t_iso_a")},
                    json={"agent_id": "ag_inv_iso", "input": "hi"})
        assert r2.status_code != 404
    finally:
        agent_store._AGENTS.pop("ag_inv_iso", None)


# ── 审批单租户隔离 ──
def test_approval_pending_scoped_by_tenant():
    t = approval_store.create_ticket("ag_x", "u_a", "d_a", "t_iso_a")
    try:
        admin = approval_store.pending_list(None, None)
        assert any(x["approval_id"] == t["approval_id"] for x in admin)   # 平台管理员全量
        assert not any(x["approval_id"] == t["approval_id"]
                       for x in approval_store.pending_list("d_b", "t_iso_b"))  # 跨租户不可见
        assert any(x["approval_id"] == t["approval_id"]
                   for x in approval_store.pending_list("d_a", "t_iso_a"))      # 同租户可见
    finally:
        approval_store._TICKETS.pop(t["approval_id"], None)


# ── 会话租户隔离 ──
def test_session_list_scoped_by_tenant():
    emp_a = CallerContext(userId="u_sess", deptId="d_a", role="employee", tenantId="t_sess_a")
    emp_b = CallerContext(userId="u_sess", deptId="d_a", role="employee", tenantId="t_sess_b")
    from app.services import session_store
    s = session_store.create_session("ag_any", emp_a, session_id="sess_iso")
    try:
        assert any(x["session_id"] == "sess_iso" for x in session_store.list_sessions(emp_a))
        assert not any(x["session_id"] == "sess_iso" for x in session_store.list_sessions(emp_b))
    finally:
        session_store._SESSIONS.pop("sess_iso", None)


# ── 租户注册表 ──
def test_tenant_create_requires_platform_admin():
    r = c.post("/api/v1/tenants", headers={"X-Caller-Context": _ctx("employee")}, json={"name": "Acme"})
    assert r.status_code == 403
    h_admin = {"X-Caller-Context": _ctx("platform_admin")}
    r2 = c.post("/api/v1/tenants", headers=h_admin, json={"name": "Acme"})
    assert r2.status_code == 200
    tid = r2.json()["data"]["tenant_id"]
    assert r2.json()["data"]["status"] == "pending"
    lst = c.get("/api/v1/tenants", headers=h_admin).json()["data"]["tenants"]
    assert any(x["tenant_id"] == tid for x in lst)
    act = c.post(f"/api/v1/tenants/{tid}/activate", headers=h_admin)
    assert act.status_code == 200 and act.json()["data"]["status"] == "active"
    tenant_store._TENANTS.pop(tid, None)


# ── 租户级 LLM 覆盖 ──
def test_tenant_llm_override_resolved():
    from app.core.config import settings
    row = tenant_store.create_tenant("OverrideCo", {"llm": {"model_id": "gpt-4o-mini"}})
    tid = row["tenant_id"]
    try:
        mid, decision = resolve_model("hi", None, None, tenant_id=tid)
        assert mid == "gpt-4o-mini"
        assert (decision or {}).get("tenant_override") is True
        # 无租户 → 不应用租户覆盖（回落路由/默认）
        mid2, _ = resolve_model("hi", None, None, tenant_id=None)
        assert mid2 != "gpt-4o-mini"
    finally:
        tenant_store._TENANTS.pop(tid, None)
