"""M21 · 登录态有效期与 401 可读性。

起因：用户看到「技能库加载失败：unauthorized: missing caller context」，同时
顶栏显示「模型未连接」。三个问题叠在一起：

1. dev token 只有 2 小时，演示到一半就全站 401
2. 401 文案只有一句话，分不清是「没带 token」还是「token 过期」——排障全靠猜
3. 前端 token 存在模块级变量里，热更新会清空，请求从此不带 Authorization

这里守 1 和 2；3 在前端 client.ts 用 localStorage 兜底（无法后端单测）。
"""
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import settings
from app.main import app

client = TestClient(app)


def _token_with_ttl(ttl: int) -> str:
    """用指定有效期签一个 dev token（改 settings 后签，签完还原）。"""
    old = settings.DEV_TOKEN_TTL_S
    settings.DEV_TOKEN_TTL_S = ttl
    try:
        return security.create_dev_token("u_1024", "d_sales", "employee")
    finally:
        settings.DEV_TOKEN_TTL_S = old


def test_dev_token_lasts_a_working_day():
    """演示 token 不能 2 小时就过期——那是做到一半突然全站 401。"""
    assert settings.DEV_TOKEN_TTL_S >= 8 * 3600
    r = client.post("/api/v1/auth/sso/callback",
                    json={"provider": "dev", "code": "dev", "device_id": "t"})
    assert r.json()["data"]["expires_in"] == settings.DEV_TOKEN_TTL_S


def test_expired_token_says_expired():
    r = client.get("/api/v1/agents", headers={"Authorization": _token_with_ttl(-10)})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]


def test_missing_token_says_missing():
    r = client.get("/api/v1/agents")
    assert r.status_code == 401
    assert "missing token" in r.json()["detail"]


def test_garbage_token_is_not_silently_accepted():
    r = client.get("/api/v1/agents", headers={"Authorization": "dev nonsense.deadbeef"})
    assert r.status_code == 401
    assert "invalid or expired" in r.json()["detail"]
