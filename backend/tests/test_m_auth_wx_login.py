"""P1 小程序身份源：POST /auth/wx/login 端点接线验证。

不依赖微信真实网络（authenticate 由 monkeypatch 注入）。覆盖：
  - 未启用 / 缺 AppID·AppSecret → 503（不误放行）
  - 启用且 code 合法 → 200，返回 sso token 且 provider=wechat
  - authenticate 抛 RuntimeError（微信侧失败）→ 502
  - /auth/settings 暴露 wx_miniapp_enabled 标志
"""
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import wx_auth

c = TestClient(app)


@pytest.fixture(autouse=True)
def _restore_wx_config():
    saved = (
        settings.WX_MINI_LOGIN_ENABLED,
        settings.WX_MINI_APP_ID,
        settings.WX_MINI_APP_SECRET,
    )
    yield
    (settings.WX_MINI_LOGIN_ENABLED,
     settings.WX_MINI_APP_ID,
     settings.WX_MINI_APP_SECRET) = saved


def _enable():
    settings.WX_MINI_LOGIN_ENABLED = True
    settings.WX_MINI_APP_ID = "wx_test_appid"
    settings.WX_MINI_APP_SECRET = "wx_test_secret"


def test_wx_login_disabled_returns_503():
    settings.WX_MINI_LOGIN_ENABLED = False
    settings.WX_MINI_APP_ID = ""
    settings.WX_MINI_APP_SECRET = ""
    r = c.post("/api/v1/auth/wx/login", json={"code": "any"})
    assert r.status_code == 503


def test_wx_login_enabled_returns_token(monkeypatch):
    _enable()
    monkeypatch.setattr(
        wx_auth, "authenticate",
        lambda code: (
            __import__("app.models.caller", fromlist=["CallerContext"])
            .CallerContext(userId="wx_o1", deptId="d_wx_mp", role="employee", tenantId="t_demo"),
            "sso xxxx.yyyy",
        ),
    )
    r = c.post("/api/v1/auth/wx/login", json={"code": "valid_code"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["token"].startswith("sso ")
    assert body["provider"] == "wechat"
    assert body["user"]["userId"] == "wx_o1"


def test_wx_login_backend_failure_returns_502(monkeypatch):
    _enable()
    monkeypatch.setattr(wx_auth, "authenticate",
                        lambda code: (_ for _ in ()).throw(RuntimeError("微信 code2Session 失败")))
    r = c.post("/api/v1/auth/wx/login", json={"code": "bad_code"})
    assert r.status_code == 502


def test_settings_exposes_wx_flag():
    _enable()
    r = c.get("/api/v1/auth/settings")
    assert r.status_code == 200
    assert r.json()["data"]["wx_miniapp_enabled"] is True
