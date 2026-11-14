"""P1-1 飞书 OAuth 身份源接入回归测试。

覆盖：
- 禁用路径：FEISHU_ENABLED 关闭或缺少 APP_ID/SECRET 时，callback 与 authorize 均返回 503。
- 正常换码：mock httpx.post（换 access_token）+ httpx.get（user_info）后，
  callback 返回前缀 `sso ` 的自签会话令牌，且租户从飞书 tenant_key 解析。
- 身份收口放行：即便 AUTH_ENFORCE_OIDC=True（生产/收口态），sso 令牌仍可被网关放行
  （属真实 SSO，区别于本地 dev 后门）。
- 网关往返：create_sso_token → decode_token 还原 CallerContext；篡改签名返回 None。
- 授权页 URL：GET /feishu/authorize 吐出含 app_id 的授权 URL（前端预留）。
- 设置暴露：/auth/settings 含 feishu_enabled 字段。

不依赖真实飞书网络；httpx 全部 monkeypatch。
"""
import httpx

from app.core.config import settings
from app.core.security import create_sso_token, decode_token
from app.main import app
from fastapi.testclient import TestClient

API = "/api/v1"


class _FakeResp:
    def __init__(self, payload: dict):
        self._p = payload

    def json(self):
        return self._p


def _fake_post(url, **kwargs):
    # 飞书换码端点 → 返回 user_access_token
    return _FakeResp({"code": 0, "data": {"access_token": "u-at-xyz"}})


def _fake_get(url, **kwargs):
    # 飞书 user_info 端点 → 返回用户身份（tenant_key 用于租户解析）
    return _FakeResp({"code": 0, "data": {
        "open_id": "ou_abc123", "name": "李飞", "email": "li@x.com",
        "tenant_key": "tenant_feishu_1"}})


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "FEISHU_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_APP_ID", "cli_id")
    monkeypatch.setattr(settings, "FEISHU_APP_SECRET", "cli_sec")
    monkeypatch.setattr(settings, "FEISHU_REDIRECT_URI", "http://localhost:5173/callback/feishu")
    monkeypatch.setattr(settings, "FEISHU_BASE_URL", "https://open.feishu.cn")
    monkeypatch.setattr(settings, "FEISHU_TENANT_ID", "")
    monkeypatch.setattr(settings, "FEISHU_DEFAULT_ROLE", "employee")
    monkeypatch.setattr(settings, "FEISHU_DEFAULT_DEPT", "d_feishu")


def test_feishu_disabled_callback_returns_503():
    # 默认（未启用）下，callback 必须拒绝，避免误把未配置的身份源当可用。
    monkeypatch_restore = {}
    orig = {k: getattr(settings, k) for k in ("FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET")}
    for k, v in orig.items():
        monkeypatch_restore[k] = v
    try:
        settings.FEISHU_ENABLED = False
        settings.FEISHU_APP_ID = ""
        settings.FEISHU_APP_SECRET = ""
        r = TestClient(app).post(f"{API}/auth/feishu/callback", json={"code": "x"})
        assert r.status_code == 503
    finally:
        for k, v in orig.items():
            setattr(settings, k, v)


def test_feishu_callback_exchange_and_userinfo(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(httpx, "post", _fake_post)
    monkeypatch.setattr(httpx, "get", _fake_get)
    r = TestClient(app).post(f"{API}/auth/feishu/callback", json={"code": "auth_code_1"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    token = body["data"]["token"]
    assert token.startswith("sso "), token
    assert body["data"]["tenant_id"] == "tenant_feishu_1"  # 租户从飞书 tenant_key 解析
    assert body["data"]["user"]["userId"] == "feishu_ou_abc123"
    assert body["data"]["user"]["role"] == "employee"
    assert body["data"]["provider"] == "feishu"


def test_feishu_authorize_url(monkeypatch):
    _enable(monkeypatch)
    r = TestClient(app).get(f"{API}/auth/feishu/authorize", params={"state": "st1"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    url = body["data"]["authorize_url"]
    assert "open.feishu.cn/open-apis/authen/v1/authorize" in url
    assert "app_id=cli_id" in url
    assert "state=st1" in url


def test_feishu_authorize_disabled_returns_503():
    orig = (settings.FEISHU_ENABLED, settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET)
    try:
        settings.FEISHU_ENABLED = False
        settings.FEISHU_APP_ID = ""
        settings.FEISHU_APP_SECRET = ""
        r = TestClient(app).get(f"{API}/auth/feishu/authorize")
        assert r.status_code == 503
    finally:
        settings.FEISHU_ENABLED, settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET = orig


def test_settings_exposes_feishu_enabled(monkeypatch):
    _enable(monkeypatch)
    r = TestClient(app).get(f"{API}/auth/settings")
    assert r.status_code == 200
    assert r.json()["data"]["feishu_enabled"] is True

    # 未启用时应为 False
    settings.FEISHU_ENABLED = False
    settings.FEISHU_APP_ID = ""
    settings.FEISHU_APP_SECRET = ""
    r2 = TestClient(app).get(f"{API}/auth/settings")
    assert r2.json()["data"]["feishu_enabled"] is False


def test_sso_token_roundtrip_and_tamper():
    caller = create_sso_token("feishu_ou_abc123", "d_feishu", "employee", "tenant_feishu_1")
    ctx = decode_token(caller)
    assert ctx is not None
    assert ctx.userId == "feishu_ou_abc123"
    assert ctx.tenantId == "tenant_feishu_1"
    assert ctx.role == "employee"

    # 篡改签名 → 验签失败返回 None
    bad = caller[:-3] + "xyz"
    assert decode_token(bad) is None


def test_sso_token_allowed_under_enforce_oidc(monkeypatch):
    # 身份收口（生产/收口态）下，真实 sso 令牌仍放行（区别于 dev 后门）。
    orig_flag = settings.AUTH_ENFORCE_OIDC
    orig_env = settings.APP_ENV
    try:
        settings.AUTH_ENFORCE_OIDC = True
        settings.APP_ENV = "prod"
        token = create_sso_token("feishu_ou_abc123", "d_feishu", "employee", "t_demo")
        assert decode_token(token) is not None
        # 对照：dev 令牌在收口态必须被拒
        from app.core.security import create_dev_token
        assert decode_token(create_dev_token("u1", "d1", "employee")) is None
    finally:
        settings.AUTH_ENFORCE_OIDC = orig_flag
        settings.APP_ENV = orig_env
