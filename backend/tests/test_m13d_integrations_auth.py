"""P1-173 · IM 路由层鉴权回归（防「CallerContext 被当 query 参数」式缺陷）。

早年实现误把 `caller: CallerContext = Query(default=None)` 写进 IM 管理端点，
既导致 401，又等于任何人都能在 query 里伪造 `role=platform_admin`。
本测试断言：鉴权必须走网关签名的 X-Caller-Context（Depends(get_caller)），
拒绝缺失 / 伪造 / 越权。
"""
from fastapi.testclient import TestClient

from app.core import security
from app.main import app
from app.models.caller import CallerContext

c = TestClient(app)


def _ctx(role: str, tenant: str = "t_demo") -> str:
    return security.encode_caller_context(
        CallerContext(userId="u1", deptId="d1", role=role, tenantId=tenant)
    )


def test_im_config_requires_auth():
    # 无任何 caller 上下文 → 401（get_caller 兜底）
    r = c.get("/api/v1/integrations/im/config")
    assert r.status_code == 401


def test_im_config_rejects_query_spoof():
    # 试图在 query 里塞 role=platform_admin 伪装管理员 → 仍 401（不读 query）
    r = c.get("/api/v1/integrations/im/config?role=platform_admin&userId=u1&deptId=d1&tenantId=t_demo")
    assert r.status_code == 401


def test_im_config_allows_platform_admin():
    h = {"X-Caller-Context": _ctx("platform_admin")}
    r = c.get("/api/v1/integrations/im/config", headers=h)
    assert r.status_code == 200
    assert r.json()["code"] == 0


def test_im_config_rejects_employee():
    h = {"X-Caller-Context": _ctx("employee")}
    r = c.get("/api/v1/integrations/im/config", headers=h)
    assert r.status_code == 403


def test_im_config_create_delete_as_admin():
    h = {"X-Caller-Context": _ctx("platform_admin")}
    # 创建
    r = c.post(
        "/api/v1/integrations/im/config", headers=h, json={
            "provider": "wecom", "corp_id": "wwx", "secret": "s3cr3t",
            "agent_id": "1", "webhook_token": "tok", "encoding_aes_key": "key43charslongxxxxxxxxxxxxxxxxxxxxx",
        },
    )
    assert r.status_code == 200
    # 密钥脱敏：列表里不应回显明文 secret
    lst = c.get("/api/v1/integrations/im/config", headers=h).json()["data"]
    assert any(x["provider"] == "wecom" and "s3cr3t" not in str(x) for x in lst)
    # 删除（清理）
    c.delete("/api/v1/integrations/im/config/wecom", headers=h)
