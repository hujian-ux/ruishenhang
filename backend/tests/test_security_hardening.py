"""安全加固回归（P0 · C1/C2/C3 + P1 · H1–H5）。

对应蓝图「身份与多租户」收口项：
  C1 网关剥离客户端 X-Caller-Context 并仅按 Authorization 翻译注入，业务层只验签；
  C2 OIDC 固定 issuer 收口（禁止自报 iss 冒用 IdP）；
  C3 dev_admin 后门仅非 prod 且未开 AUTH_ENFORCE_OIDC 时可用；
  H1 file_store 跨租户拦截；H2 import_agents 归属当前租户；
  H3 草稿/工作流跨租户不可见/不可运行；H4 审批决策跨租户拒绝；
  H5 审计查询按租户收敛。

所有「客户端直塞 X-Caller-Context」的路径均被网关剥离，必须改为持 Authorization
（dev token / OIDC）走真实网关翻译链路——这也是生产形态。
"""
import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import settings
from app.main import app
from app.models.caller import CallerContext
from app.services import agent_builder, agent_store, approval_store, audit, workflows

c = TestClient(app)


# ── C1 · 网关闭权 + 业务层验签 ──────────────────────────────────────────────

def test_decode_caller_context_rejects_forged_signature():
    # 客户端不知签名密钥，篡改签名为任意值 → 必须拒绝（返回 None）
    good = security.encode_caller_context(
        CallerContext(userId="u1", deptId="d1", role="employee", tenantId="t1"))
    raw, _ = good.split(".", 1)
    forged = f"{raw}.deadbeef"
    assert security.decode_caller_context(forged) is None


def test_decode_caller_context_accepts_valid():
    ctx = CallerContext(userId="u1", deptId="d1", role="employee", tenantId="t1")
    enc = security.encode_caller_context(ctx)
    out = security.decode_caller_context(enc)
    assert out is not None
    assert out.userId == "u1" and out.tenantId == "t1" and out.role == "employee"


def test_gateway_strips_client_supplied_x_caller_context():
    # 即使客户端用「正确的网关密钥」自签 X-Caller-Context（模拟密钥泄露），
    # 网关也必须剥离、只认 Authorization —— 否则 401。
    signed = security.encode_caller_context(
        CallerContext(userId="u1", deptId="d1", role="platform_admin", tenantId="t1"))
    # 仅带 X-Caller-Context，无 Authorization
    r = c.get("/api/v1/integrations/im/config", headers={"X-Caller-Context": signed})
    assert r.status_code == 401


def test_gateway_translates_authorization_to_context():
    # 持 Authorization（dev token）→ 网关翻译注入 → 下游鉴权生效（员工非管理员→403，证明 caller 已就位）
    tok = security.create_dev_token(user_id="u1", dept_id="d1", role="employee", tenant_id="t1")
    r = c.get("/api/v1/integrations/im/config", headers={"Authorization": tok})
    assert r.status_code == 403  # 鉴权通过但角色不足，而非 401（无 caller）


# ── C2 · OIDC 固定 issuer 收口 ─────────────────────────────────────────────

def test_oidc_empty_issuer_rejected():
    # 未配置 OIDC_ISSUER 时，任何自报 iss 的 token 一律拒绝（杜绝 IdP 冒用）
    assert security._verify_oidc_token("a.b.c") is None


def test_oidc_valid_token_with_configured_issuer_and_jwks():
    issuer = "https://idp.example.com"
    audience = "my-aud"
    from cryptography.hazmat.primitives.asymmetric import rsa
    import base64
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()

    def _b64uint(x: int) -> str:
        length = (x.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(x.to_bytes(length, "big")).rstrip(b"=").decode("ascii")

    jwk = {"kty": "RSA", "kid": "k1", "alg": "RS256",
           "n": _b64uint(pub.n), "e": _b64uint(pub.e)}
    # 注入 JWKS 缓存，旁路网络（单元测试不触发 discovery）
    security._JWKS_CACHE[issuer] = ({"keys": [jwk]}, time.time())
    try:
        token = jwt.encode(
            {"iss": issuer, "sub": "u_oidc", "aud": audience, "exp": int(time.time()) + 3600,
             "preferred_username": "alice", "organization": ["org_x"]},
            key, algorithm="RS256", headers={"kid": "k1"},
        )
        caller = security._verify_oidc_token(token, issuer=issuer, audience=audience)
        assert caller is not None
        assert caller.userId == "alice"
        # 篡改签名 → 拒绝
        forged = token.rsplit(".", 1)[0] + ".tampered"
        assert security._verify_oidc_token(forged, issuer=issuer, audience=audience) is None
    finally:
        security._JWKS_CACHE.pop(issuer, None)


# ── C3 · dev_admin 后门门控 ────────────────────────────────────────────────

def _callback_dev_admin(tok=None):
    h = {"Authorization": tok} if tok else {}
    return c.post("/api/v1/auth/sso/callback",
                  headers=h, json={"provider": "wecom", "code": "dev_admin",
                                  "device_id": "dev", "state": "x"})


def test_dev_admin_allowed_in_dev_default(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    monkeypatch.setattr(settings, "AUTH_ENFORCE_OIDC", False)
    r = _callback_dev_admin()
    assert r.status_code == 200
    assert r.json()["data"]["user"]["role"] == "platform_admin"


def test_dev_admin_rejected_when_prod(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "AUTH_ENFORCE_OIDC", False)
    assert _callback_dev_admin().status_code == 403


def test_dev_admin_rejected_when_enforce_oidc(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    monkeypatch.setattr(settings, "AUTH_ENFORCE_OIDC", True)
    assert _callback_dev_admin().status_code == 403


# ── H1 · file_store 跨租户拦截 ─────────────────────────────────────────────

def _rec(tenant, owner="u1", dept="d1"):
    return {"tenant_id": tenant, "owner_id": owner, "dept_id": dept, "file_id": "f1"}


def test_file_store_cross_tenant_rejected():
    emp_b = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="t_b")
    rec_a = _rec("t_a", owner="u_a")
    assert not file_store_can_access(rec_a, emp_b)  # 跨租户不可见
    # 平台管理员全域可见
    admin = CallerContext(userId="u_p", deptId="d_p", role="platform_admin", tenantId="")
    assert file_store_can_access(rec_a, admin)


def test_file_store_same_tenant_owner_allowed():
    emp_a = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_a")
    rec_a = _rec("t_a", owner="u_a")
    assert file_store_can_access(rec_a, emp_a)


def file_store_can_access(rec, caller):
    from app.services import file_store
    return file_store.can_access(rec, caller)


# ── H2 · import_agents 归属当前租户 ────────────────────────────────────────

def test_import_agents_assigns_tenant():
    tok = security.create_dev_token(user_id="u_h2", dept_id="d_h2", role="platform_admin",
                                    tenant_id="t_h2")
    body = {"items": [{
        "name": "H2安全测试包_唯一名", "description": "d", "system_prompt": "sp",
        "adapter_type": "llm", "knowledge_scope": "department",
        "source_path": "solutions/h2_unique",
    }]}
    r = c.post("/api/v1/agents/import", headers={"Authorization": tok}, json=body)
    assert r.status_code == 200
    created = r.json()["data"]["agents"]
    assert created and len(created) == 1
    aid = created[0]["agent_id"]
    try:
        saved = agent_store.get(aid)
        assert saved is not None
        assert saved["tenant_id"] == "t_h2"  # 导入即归属当前租户，不会被当平台级
    finally:
        agent_store._AGENTS.pop(aid, None)


# ── H3 · 草稿/工作流 跨租户隔离 ────────────────────────────────────────────

def test_draft_scoped_by_tenant_and_publish_gated():
    ca = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_h3a")
    cb = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="t_h3b")
    admin = CallerContext(userId="u_p", deptId="d_p", role="platform_admin", tenantId="t_p")
    draft = agent_builder.build_agent("H3草稿", "goal", "in", "out", ["s1"], ca)
    aid = draft["agent_id"]
    try:
        assert draft["tenant_id"] == "t_h3a"
        # 跨租户员工看不到（H3：草稿按租户收敛）
        assert agent_builder.list_drafts(cb) == []
        # 平台管理员看得到
        assert any(d["agent_id"] == aid for d in agent_builder.list_drafts(admin))
        # 非平台管理员禁止发布（无论是否同租户）
        with pytest.raises(Exception):
            agent_builder.publish(aid, cb)
    finally:
        agent_builder._DRAFTS.delete(aid)


def test_workflow_scoped_by_tenant_and_run_cross_tenant_forbidden():
    ca = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_h3a")
    cb = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="t_h3b")
    wf = workflows.create_workflow("H3流", [{"type": "llm", "name": "n"}], ca)
    wid = wf["id"]
    try:
        assert wf["tenant_id"] == "t_h3a"
        assert workflows.list_workflows(cb) == []  # 跨租户不可见
        # 跨租户运行 → 403（H3 守卫）
        with pytest.raises(Exception):
            workflows.run_workflow(wid, "x", cb)
    finally:
        workflows._WF.delete(wid)


# ── H4 · 审批决策跨租户拒绝 ───────────────────────────────────────────────

def test_decide_cross_tenant_forbidden():
    t = approval_store.create_ticket("ag_x", "u_a", "d_a", "t_h4a")
    try:
        # 同租户 dept_admin 可决策
        approval_store.decide(t["approval_id"], "approve", "u_a2", "dept_admin", "d_a",
                              decider_tenant="t_h4a")
        # 跨租户 dept_admin 决策 → 拒绝
        t2 = approval_store.create_ticket("ag_y", "u_b", "d_b", "t_h4a")
        with pytest.raises(approval_store.ForbiddenDecider):
            approval_store.decide(t2["approval_id"], "approve", "u_b2", "dept_admin", "d_b",
                                  decider_tenant="t_other")
    finally:
        approval_store._TICKETS.pop(t["approval_id"], None)
        approval_store._TICKETS.pop(t2["approval_id"], None)


def test_can_decide_at_stage_cross_tenant_false():
    ticket = {"tenant_id": "t_h4a", "dept_id": "d_a", "stages": None, "current_stage": 0}
    # 跨租户 dept_admin 不能审
    assert approval_store._can_decide_at_stage(ticket, "dept_admin", "d_a", "t_other") is False
    # 同租户 dept_admin 可审
    assert approval_store._can_decide_at_stage(ticket, "dept_admin", "d_a", "t_h4a") is True


# ── H5 · 审计查询按租户收敛 ───────────────────────────────────────────────

def test_audit_get_logs_scoped_by_tenant():
    ca = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_h5a")
    cb = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="t_h5b")
    audit.record("download", ca, target="tgt_a", result="ok")
    audit.record("download", cb, target="tgt_b", result="ok")
    rows_a = audit.get_logs(ca)
    rows_b = audit.get_logs(cb)
    # 租户 A 看不到租户 B 的日志
    assert all(r["tenant_id"] == "t_h5a" for r in rows_a)
    assert all(r["tenant_id"] == "t_h5b" for r in rows_b)
    assert any(r["target"] == "tgt_a" for r in rows_a)
    assert not any(r["target"] == "tgt_b" for r in rows_a)
