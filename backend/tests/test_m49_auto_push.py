"""M49 · 审批待办自动推（IM 自动回推当前级对应审批人）。

覆盖：
- resolve_approver_recipients：角色级 / 部门级 / 通配 / 默认单级回退 / 无通知人。
- auto_notify_pending：无 notifiers 跳过、无 IM 配置跳过、配置后真实回推、找不到单据跳过。
- 路由层接线：开新单 / 多级流转触发 auto_notify_pending_async（同步化以便断言）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import approval_store, im_gateway, im_store, tenant_store

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")}


def _tenant(cfg):
    r = client.post("/api/v1/tenants",
                    json={"name": "T49_" + os.urandom(3).hex(), "config": cfg},
                    headers=_admin())
    assert r.status_code == 200, r.text
    return r.json()["data"]["tenant_id"]


def _register(tid, dept="d_sales"):
    tok = {"Authorization": create_dev_token("u_emp", dept, "employee", tid)}
    r = client.post("/api/v1/agents",
                    json={"name": "M49", "adapter_type": "llm_retrieval",
                          "description": "x", "knowledge_scope": "department"},
                    headers=tok)
    assert r.status_code == 200, r.text
    return r.json()["data"]["approval_id"], tok


# ── resolve_approver_recipients ──────────────────────────────────────────────
def test_resolve_no_notifiers_returns_empty():
    tid = _tenant({})
    t = approval_store.create_ticket("ag1", "u", "d", tid)
    assert im_gateway.resolve_approver_recipients(tid, t) == []


def test_resolve_role_level():
    tid = _tenant({"approval": {"notifiers": {
        "dept_admin": ["alice"], "platform_admin": ["boss"]}}})
    t = approval_store.create_ticket("ag1", "u", "d_sales", tid)
    assert im_gateway.resolve_approver_recipients(tid, t) == ["alice"]
    t2 = approval_store.create_ticket(
        "ag2", "u", "d_sales", tid,
        stages=[{"name": "部门", "approver_role": "dept_admin"},
                {"name": "平台", "approver_role": "platform_admin"}])
    # 当前级仍是第 0 级（部门）→ alice
    assert im_gateway.resolve_approver_recipients(tid, t2) == ["alice"]


def test_resolve_dept_override_and_wildcard():
    tid = _tenant({"approval": {"notifiers": {
        "dept:d_sales": ["carol"], "dept_admin": ["alice"], "*": ["anyone"]}}})
    t = approval_store.create_ticket("ag1", "u", "d_sales", tid)
    assert im_gateway.resolve_approver_recipients(tid, t) == ["carol", "alice", "anyone"]
    t2 = approval_store.create_ticket("ag2", "u", "d_other", tid)
    # 部门不同 → 无部门专属，命中角色 + 通配
    assert im_gateway.resolve_approver_recipients(tid, t2) == ["alice", "anyone"]


def test_resolve_default_single_stage_uses_dept_admin():
    tid = _tenant({"approval": {"notifiers": {"dept_admin": ["alice"]}}})
    t = approval_store.create_ticket("ag1", "u", "d_sales", tid)  # 无 stages → 默认单级
    assert im_gateway.resolve_approver_recipients(tid, t) == ["alice"]


# ── auto_notify_pending ──────────────────────────────────────────────────────
def test_auto_notify_no_notifiers():
    tid = _tenant({})
    t = approval_store.create_ticket("ag1", "u", "d", tid)
    res = im_gateway.auto_notify_pending(tid, t["approval_id"])
    assert res["skipped"] == 1 and res["reason"] == "no_notifiers"


def test_auto_notify_no_im_config():
    tid = _tenant({"approval": {"notifiers": {"dept_admin": ["alice"]}}})
    t = approval_store.create_ticket("ag1", "u", "d", tid)
    res = im_gateway.auto_notify_pending(tid, t["approval_id"])
    assert res["reason"] == "no_im_config" and res["skipped"] == 1


def test_auto_notify_pushes_to_recipients():
    tid = _tenant({"approval": {"notifiers": {
        "dept_admin": ["alice"], "platform_admin": ["boss"]}}})
    im_store.save_config(tid, "wecom", corp_id="c", secret="s", agent_id="1", webhook_token="t")
    calls = []
    def fake(url, data, headers):
        calls.append((url, data))
        return {"access_token": "TOK", "errcode": 0}  # 同时供 gettoken + 发消息
    im_gateway.set_http_post(fake)
    try:
        t = approval_store.create_ticket("ag1", "u", "d", tid)
        res = im_gateway.auto_notify_pending(tid, t["approval_id"], base_url="https://app.example.com")
    finally:
        im_gateway.set_http_post(lambda u, d, h: {})
    assert res["skipped"] == 0
    assert len(res["pushed"]) == 1 and res["pushed"][0]["to_user"] == "alice"
    msg_calls = [c for c in calls if isinstance(c[1], dict) and "markdown" in c[1]]
    assert msg_calls, "应下发 markdown OA 卡片"
    assert t["approval_id"] in str(msg_calls[0][1])


def test_auto_notify_pushes_to_all_configured_providers():
    tid = _tenant({"approval": {"notifiers": {"dept_admin": ["alice"]}}})
    im_store.save_config(tid, "wecom", corp_id="c", secret="s", agent_id="1", webhook_token="t")
    im_store.save_config(tid, "dingtalk", corp_id="k", secret="s2", webhook_token="wt")
    im_gateway.set_http_post(lambda u, d, h: {"access_token": "TOK", "errcode": 0})
    try:
        t = approval_store.create_ticket("ag1", "u", "d", tid)
        res = im_gateway.auto_notify_pending(tid, t["approval_id"])
    finally:
        im_gateway.set_http_post(lambda u, d, h: {})
    providers = {p["provider"] for p in res["pushed"]}
    assert providers == {"wecom", "dingtalk"}


def test_auto_notify_unknown_ticket():
    tid = _tenant({})
    res = im_gateway.auto_notify_pending(tid, "apr_nope")
    assert res["skipped"] == 1 and res["reason"] == "ticket_not_found"


# ── 路由层接线（同步化 auto_notify_pending_async 以便断言） ───────────────────
def test_router_auto_push_on_register(monkeypatch):
    tid = _tenant({"approval": {"notifiers": {"dept_admin": ["alice"]}}})
    captured = {}
    def fake_async(tenant, apr_id, base_url=""):
        captured["tenant"] = tenant
        captured["apr"] = apr_id
        return im_gateway.auto_notify_pending(tenant, apr_id, base_url)
    monkeypatch.setattr(im_gateway, "auto_notify_pending_async", fake_async)
    apr_id, _ = _register(tid)
    assert captured.get("apr") == apr_id and captured.get("tenant") == tid


def test_router_auto_push_on_stage_advance(monkeypatch):
    tid = _tenant({"approval": {"stages": [
        {"name": "部门", "approver_role": "dept_admin"},
        {"name": "平台", "approver_role": "platform_admin"}],
        "notifiers": {"dept_admin": ["alice"], "platform_admin": ["boss"]}}})
    captured = []
    def fake_async(tenant, apr_id, base_url=""):
        captured.append((tenant, apr_id))
        return None
    monkeypatch.setattr(im_gateway, "auto_notify_pending_async", fake_async)
    apr_id, _ = _register(tid)
    captured.clear()  # 清掉开单那次，只看流转
    dadmin = {"Authorization": create_dev_token("u_da", "d_sales", "dept_admin", tid)}
    r = client.post(f"/api/v1/approval/{apr_id}/decide",
                    json={"decision": "approve"}, headers=dadmin)
    assert r.status_code == 200, r.text
    assert any(c[1] == apr_id for c in captured), "流转到下一级应触发自动回推"


def test_router_no_auto_push_on_final_or_reject(monkeypatch):
    tid = _tenant({"approval": {"stages": [
        {"name": "部门", "approver_role": "dept_admin"}],
        "notifiers": {"dept_admin": ["alice"]}}})
    captured = []
    def fake_async(tenant, apr_id, base_url=""):
        captured.append(apr_id)
        return None
    monkeypatch.setattr(im_gateway, "auto_notify_pending_async", fake_async)
    apr_id, _ = _register(tid)
    captured.clear()
    dadmin = {"Authorization": create_dev_token("u_da", "d_sales", "dept_admin", tid)}
    # 单级末级 approve → 终态，不应再自动回推
    r = client.post(f"/api/v1/approval/{apr_id}/decide",
                    json={"decision": "approve"}, headers=dadmin)
    assert r.status_code == 200
    assert captured == [], "终态（发布）不应触发回推"
