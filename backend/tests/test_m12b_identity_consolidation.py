"""P1-173a 身份收口：AUTH_ENFORCE_OIDC 开启后拒绝 dev token、sso/callback 拒 dev_admin 捷径。

覆盖：
- decode_token 在收口态拒绝 dev token（含合法 dev token）。
- sso/callback 在收口态对 dev_admin 返回 403。
- sso/callback 带 id_token 时走验签分支（注入 _verify_oidc_token 避开真 RSA）。
不依赖真实 IdP / RSA 密钥。
"""
import app.core.security as security
from app.core.security import create_dev_token, decode_token
from app.core.config import settings
from fastapi.testclient import TestClient
from app.main import app


def _client():
    return TestClient(app)


def test_decode_token_rejects_dev_when_enforced():
    dev = create_dev_token("u1", "d1", "employee")
    orig = settings.AUTH_ENFORCE_OIDC
    settings.AUTH_ENFORCE_OIDC = True
    try:
        assert decode_token(dev) is None
    finally:
        settings.AUTH_ENFORCE_OIDC = orig


def test_decode_token_accepts_dev_when_not_enforced():
    dev = create_dev_token("u1", "d1", "employee")
    orig = settings.AUTH_ENFORCE_OIDC
    settings.AUTH_ENFORCE_OIDC = False
    try:
        c = decode_token(dev)
        assert c is not None and c.userId == "u1"
    finally:
        settings.AUTH_ENFORCE_OIDC = orig


def test_sso_callback_dev_admin_rejected_when_enforced():
    orig = settings.AUTH_ENFORCE_OIDC
    settings.AUTH_ENFORCE_OIDC = True
    try:
        r = _client().post("/api/v1/auth/sso/callback",
                           json={"provider": "wecom", "code": "dev_admin", "device_id": "x"})
        assert r.status_code == 403
        assert "身份收口" in r.json().get("detail", "")
    finally:
        settings.AUTH_ENFORCE_OIDC = orig


def test_sso_callback_dev_admin_allowed_when_not_enforced():
    orig = settings.AUTH_ENFORCE_OIDC
    settings.AUTH_ENFORCE_OIDC = False
    try:
        r = _client().post("/api/v1/auth/sso/callback",
                           json={"provider": "wecom", "code": "dev_admin", "device_id": "x"})
        assert r.status_code == 200
        assert r.json()["data"]["user"]["role"] == "platform_admin"
    finally:
        settings.AUTH_ENFORCE_OIDC = orig


def test_sso_callback_id_token_branch_verifies(monkeypatch):
    # auth.py 在导入时绑定了 _verify_oidc_token，须 patch auth 模块的属性（非 security 模块）
    import app.routers.auth as auth_mod
    from app.models.caller import CallerContext
    monkeypatch.setattr(auth_mod, "_verify_oidc_token",
                        lambda tok: CallerContext(userId="u_real", deptId="d_real",
                                                  role="dept_admin", tenantId="t_demo"))
    r = _client().post("/api/v1/auth/sso/callback",
                       json={"provider": "wecom", "code": "x", "device_id": "x",
                             "id_token": "fake.jwt.token"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["token"] == "fake.jwt.token"   # 原样回传作 Bearer，网关走 OIDC 验签
    assert d["user"]["userId"] == "u_real"
    assert d["user"]["role"] == "dept_admin"
