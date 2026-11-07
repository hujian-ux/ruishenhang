"""M43b · 租户级 SSO(brand) 配置接到 Casdoor 多组织 + 品牌下发。

- 租户 sso.casdoor_org ↔ Casdoor organization 映射（resolve_tenant_by_org）。
- OIDC claims → tenant_id 纯映射（resolve_tenant_from_oidc_claims，零行为变更）。
- 品牌下发：/auth/settings 平台默认、/auth/branding 当前租户、/tenants/{id}/branding 管理 CRUD。
- SSO 回调尽力回传 tenant_id（命中租户表才返回）。
"""
import jwt

from fastapi.testclient import TestClient

import pytest

from app.core import security
from app.main import app
from app.models.caller import CallerContext
from app.services import tenant_store

c = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _trigger_startup():
    with c:
        yield


def _ctx(role, tenant="t_demo", userId="u1", deptId="d1"):
    return security.encode_caller_context(
        CallerContext(userId=userId, deptId=deptId, role=role, tenantId=tenant)
    )


# ── 组织 ↔ 租户映射 ──
def test_resolve_tenant_by_org():
    t = tenant_store.create_tenant("OrgCo", {"sso": {"casdoor_org": "acme-org"}})
    try:
        assert tenant_store.resolve_tenant_by_org("acme-org") == t["tenant_id"]
        assert tenant_store.resolve_tenant_by_org("nope") is None
        assert tenant_store.resolve_tenant_by_org("") is None
    finally:
        tenant_store._TENANTS.pop(t["tenant_id"], None)


def test_resolve_tenant_from_oidc_claims():
    t = tenant_store.create_tenant("OrgCo2", {"sso": {"casdoor_org": "beta-org"}})
    try:
        claims_hit = {"organization": "beta-org"}
        assert security.resolve_tenant_from_oidc_claims(claims_hit) == t["tenant_id"]
        # Casdoor 有时以数组形式下发 organization
        claims_arr = {"organization": ["beta-org", "x"]}
        assert security.resolve_tenant_from_oidc_claims(claims_arr) == t["tenant_id"]
        # 未命中 → None（回落网关/默认）
        assert security.resolve_tenant_from_oidc_claims({"organization": "ghost"}) is None
        assert security.resolve_tenant_from_oidc_claims({}) is None
    finally:
        tenant_store._TENANTS.pop(t["tenant_id"], None)


# ── 品牌回落 ──
def test_get_branding_default_and_override():
    d = tenant_store.get_branding(None)
    assert d["name"] == "企业 AI 智能体中台"
    assert d["primary_color"] == "#1677ff"
    t = tenant_store.create_tenant("BrandCo", {"brand": {"name": "甲方科技", "primary_color": "#0a0"}})
    try:
        b = tenant_store.get_branding(t["tenant_id"])
        assert b["name"] == "甲方科技"          # 覆盖
        assert b["primary_color"] == "#0a0"      # 覆盖
        assert "logo_url" in b                   # 回落默认键仍在
    finally:
        tenant_store._TENANTS.pop(t["tenant_id"], None)


# ── 公开设置暴露平台品牌 ──
def test_auth_settings_exposes_platform_branding():
    r = c.get("/api/v1/auth/settings")
    assert r.status_code == 200
    assert r.json()["data"]["platform_branding"]["name"]


# ── 登录后取当前租户品牌 ──
def test_current_branding_authenticated():
    t = tenant_store.create_tenant("BrandCo2", {"brand": {"name": "乙方制造"}})
    try:
        r = c.get("/api/v1/auth/branding",
                  headers={"X-Caller-Context": _ctx("employee", tenant=t["tenant_id"])})
        assert r.status_code == 200
        assert r.json()["data"]["branding"]["name"] == "乙方制造"
        # 平台管理员（t_platform）回落平台默认
        r2 = c.get("/api/v1/auth/branding",
                   headers={"X-Caller-Context": _ctx("platform_admin", tenant="t_platform")})
        assert r2.json()["data"]["branding"]["name"] == "企业 AI 智能体中台"
    finally:
        tenant_store._TENANTS.pop(t["tenant_id"], None)


# ── 租户品牌管理 CRUD（platform_admin）──
def test_tenant_branding_admin_crud():
    t = tenant_store.create_tenant("BrandCo3", {})
    h_admin = {"X-Caller-Context": _ctx("platform_admin", tenant="t_platform")}
    try:
        # 初始：平台默认
        g0 = c.get(f"/api/v1/tenants/{t['tenant_id']}/branding", headers=h_admin)
        assert g0.status_code == 200 and g0.json()["data"]["branding"]["name"]
        # 设置品牌 + casdoor_org
        p = c.put(f"/api/v1/tenants/{t['tenant_id']}/branding",
                  json={"name": "丙方集团", "logo_url": "https://x/y.png",
                        "primary_color": "#abcabc", "casdoor_org": "gamma-org"},
                  headers=h_admin)
        assert p.status_code == 200
        cfg = p.json()["data"]["config"]
        assert cfg["brand"]["name"] == "丙方集团"
        assert cfg["sso"]["casdoor_org"] == "gamma-org"
        # 读回确认持久
        g1 = c.get(f"/api/v1/tenants/{t['tenant_id']}/branding", headers=h_admin)
        assert g1.json()["data"]["branding"]["primary_color"] == "#abcabc"
        # casdoor_org 映射即时生效
        assert tenant_store.resolve_tenant_by_org("gamma-org") == t["tenant_id"]
        # 非管理员 403
        h_emp = {"X-Caller-Context": _ctx("employee", tenant=t["tenant_id"])}
        assert c.get(f"/api/v1/tenants/{t['tenant_id']}/branding", headers=h_emp).status_code == 403
        # 未知租户 404
        assert c.get("/api/v1/tenants/t_noexist/branding", headers=h_admin).status_code == 404
    finally:
        tenant_store._TENANTS.pop(t["tenant_id"], None)


# ── SSO 回调尽力回传 tenant_id ──
def test_sso_callback_returns_tenant_id(monkeypatch):
    t = tenant_store.create_tenant("SSOCo", {"sso": {"casdoor_org": "delta-org"}})
    fake = jwt.encode({"organization": "delta-org"}, key="k", algorithm="HS256")
    monkeypatch.setattr("app.routers.auth._verify_oidc_token",
                        lambda tok, issuer="", audience="": CallerContext(
                            userId="u", deptId="d", role="employee", tenantId="t_demo"))
    try:
        r = c.post("/api/v1/auth/sso/callback",
                   json={"provider": "wecom", "code": "x", "device_id": "d", "id_token": fake})
        assert r.status_code == 200
        assert r.json()["data"]["tenant_id"] == t["tenant_id"]
        # 未命中组织 → tenant_id 为 None（不误导前端）
        fake2 = jwt.encode({"organization": "ghost"}, key="k", algorithm="HS256")
        r2 = c.post("/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": "x", "device_id": "d", "id_token": fake2})
        assert r2.json()["data"]["tenant_id"] is None
    finally:
        tenant_store._TENANTS.pop(t["tenant_id"], None)
