"""P0 Casdoor 收口补强：默认值 fail-closed + 生产态网关拒绝 dev token。

覆盖：
- config 默认逻辑：APP_ENV=prod 且未显式设 AUTH_ENFORCE_OIDC → 默认收口（True）。
- 网关层 decode_token：即便 AUTH_ENFORCE_OIDC=False，只要处于 prod 也拒绝 dev token
  （杜绝「忘记开开关就暴露 dev 后门」）。
- 前端 fail-closed：Login 默认 enforce=true（由 Login.tsx 实现，此处校验 /auth/settings
  在 prod 默认态返回 enforce_oidc=true）。
不依赖真实 IdP / RSA 密钥。
"""
import importlib

from app.core.config import settings
from app.core.security import create_dev_token, decode_token
from fastapi.testclient import TestClient

from app.main import app


def _client():
    return TestClient(app)


def test_config_default_auto_enforce_in_prod(monkeypatch):
    # 验证 config 默认逻辑：prod 且未显式设 AUTH_ENFORCE_OIDC → 默认收口（True）
    import app.core.config as cfg_mod
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("AUTH_ENFORCE_OIDC", raising=False)
    importlib.reload(cfg_mod)
    try:
        assert cfg_mod.settings.AUTH_ENFORCE_OIDC is True
    finally:
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.delenv("AUTH_ENFORCE_OIDC", raising=False)
        importlib.reload(cfg_mod)


def test_decode_token_rejects_dev_in_prod_even_without_explicit_flag():
    # 收口默认 fail-closed：APP_ENV=prod 且「忘记开开关」时，dev token 在网关层仍被拒
    dev = create_dev_token("u1", "d1", "employee")
    orig_env = settings.APP_ENV
    orig_flag = settings.AUTH_ENFORCE_OIDC
    settings.APP_ENV = "prod"
    settings.AUTH_ENFORCE_OIDC = False   # 模拟生产但漏设显式开关
    try:
        assert decode_token(dev) is None
    finally:
        settings.APP_ENV = orig_env
        settings.AUTH_ENFORCE_OIDC = orig_flag


def test_auth_settings_reports_enforce_true_in_prod_default(monkeypatch):
    # prod 默认态（未显式关）下，前端依赖的 /auth/settings 必须回传 enforce_oidc=true
    import app.core.config as cfg_mod
    import app.routers.auth as auth_mod
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("AUTH_ENFORCE_OIDC", raising=False)
    importlib.reload(cfg_mod)
    try:
        # 刷新 auth 模块对 settings 的引用（auth 在导入时绑定了 settings 对象）
        import app.core.security as sec_mod
        monkeypatch.setattr(auth_mod, "settings", cfg_mod.settings)
        monkeypatch.setattr(sec_mod, "settings", cfg_mod.settings)
        r = _client().get("/api/v1/auth/settings")
        assert r.status_code == 200
        assert r.json()["data"]["enforce_oidc"] is True
    finally:
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.delenv("AUTH_ENFORCE_OIDC", raising=False)
        importlib.reload(cfg_mod)
        monkeypatch.setattr(auth_mod, "settings", cfg_mod.settings)
        monkeypatch.setattr(sec_mod, "settings", cfg_mod.settings)
