"""M49 · 模型在线配置。

背景：平台交付后最常见的卡点是"顶部一直显示模型未连接"，而原来只能改 .env 重启。
这一层要守住四件事：

1. 只有平台管理员能改（密钥入口不能对员工开放）；
2. 改完**当次进程就生效**（settings 是 import 时快照，只写 os.environ 没用）；
3. 重启后仍生效（落盘 .env.local，由 config._load_env_files 读回）；
4. 换厂商不能把请求路由回旧模型名 —— 否则带着新 key 去调 glm，直接 401，
   用户只会看到"配了没用"。

不联网：本文件不打真模型（真生成探活由 /models/probe 负责，另行手工验收）。
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.services import runtime_config

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_1024", "d_sales", "employee")}

_TOUCHED = (
    "LLM_PROVIDER", "LLM_VENDOR", "LLM_BASE_URL", "LLM_API_KEY", "LLM_DEFAULT_MODEL",
    "ROUTING_ENABLED", "ROUTING_SMALL_MODEL", "ROUTING_LARGE_MODEL",
    "LITELLM_BASE_URL", "LITELLM_MASTER_KEY", "ZHIPU_API_KEY", "DASHSCOPE_API_KEY",
    "LLM_TEMPERATURE", "LLM_MAX_TOKENS",
)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """配置端点会改全局 settings + os.environ，还会写 .env.local。

    不隔离的话：① 后面的用例跑在"已配好智谱"的世界里；② 测试会往真实
    backend/.env.local 写假 key（真跑一遍就把开发机的配置覆盖了）。
    """
    from app.core.config import settings
    from app.services import model_gateway

    saved_env = {k: os.environ.get(k) for k in _TOUCHED}
    saved_attr = {k: getattr(settings, k) for k in _TOUCHED if hasattr(settings, k)}
    # 配新模型会自动登记进全局注册表；不还原的话后面的用例会看到多出来的模型
    saved_registry = dict(model_gateway._REGISTRY)

    env_file = tmp_path / ".env.local"
    # 端点调用 runtime_config.persist(updates)，这里换成写 tmp_path：
    # 既不碰真实 backend/.env.local，又保留"落盘"这一环的可测性。
    monkeypatch.setattr(runtime_config, "persist",
                        lambda updates, path=None: _write(env_file, updates))
    yield env_file
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k, v in saved_attr.items():
        setattr(settings, k, v)
    model_gateway._REGISTRY.clear()
    model_gateway._REGISTRY.update(saved_registry)


def _write(path, updates):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keys = set(updates)
    out = [ln for ln in lines
           if not (ln.strip() and not ln.strip().startswith("#") and "=" in ln
                   and ln.split("=", 1)[0].strip() in keys)]
    out += [f"{k}={v}" for k, v in updates.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return str(path)


def test_employee_cannot_configure_model():
    """密钥入口只对平台管理员开放。"""
    r = client.put("/api/v1/models/config", headers=EMP,
                   json={"provider": "direct", "vendor": "zhipu", "model": "glm-4.6"})
    assert r.status_code == 403


def test_direct_config_takes_effect_immediately(_isolate_config):
    """保存即生效：settings 立刻是新值（不等重启），且已落盘。"""
    from app.core.config import settings

    r = client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "zhipu", "api_key": "sk-test-1234567890",
        "model": "glm-4.6", "routing": {"enabled": False},
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["gateway"]["provider"] == "direct"
    assert data["gateway"]["key_configured"] is True

    assert settings.LLM_PROVIDER == "direct"
    assert settings.LLM_VENDOR == "zhipu"
    assert settings.LLM_DEFAULT_MODEL == "glm-4.6"
    assert settings.LLM_API_KEY == "sk-test-1234567890"
    # 布尔必须被 coerce 成真 bool：否则字符串 "0" 是真值，开关等于没关
    assert settings.ROUTING_ENABLED is False

    # 落盘（tmp_path 里的 .env.local，重启后由 _load_env_files 读回）
    txt = _isolate_config.read_text(encoding="utf-8")
    assert "LLM_DEFAULT_MODEL=glm-4.6" in txt
    assert "LLM_PROVIDER=direct" in txt


def test_blank_key_keeps_existing(_isolate_config):
    """只想换模型名时不能把已有 key 清空。"""
    from app.core.config import settings

    client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "zhipu", "api_key": "sk-keep-me-0001", "model": "glm-4.6",
    })
    r = client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "zhipu", "api_key": "", "model": "glm-4-plus",
    })
    assert r.status_code == 200
    assert settings.LLM_DEFAULT_MODEL == "glm-4-plus"
    assert settings.LLM_API_KEY == "sk-keep-me-0001"


def test_custom_vendor_requires_base_url():
    """自建/私有化不给地址的话请求会打到空地址，报错极难懂 —— 前置拦掉。"""
    r = client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "custom", "model": "my-local-model",
    })
    assert r.status_code == 422
    assert "base_url" in r.json()["detail"]


def test_routing_follows_configured_model(_isolate_config):
    """换厂商不做路由对齐 → 请求被改写回 glm-4.6，用别家 key 直接 401。"""
    from app.core.config import settings

    r = client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "deepseek",
        "api_key": "sk-deepseek-xxxx", "model": "deepseek-chat",
        "routing": {"enabled": True},
    })
    assert r.status_code == 200
    assert settings.ROUTING_SMALL_MODEL == "deepseek-chat"
    assert settings.ROUTING_LARGE_MODEL == "deepseek-chat"
    assert settings.ROUTING_ENABLED is True

    # 显式给两档时以用户为准
    client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "deepseek", "model": "deepseek-chat",
        "routing": {"enabled": True, "small": "deepseek-chat", "large": "deepseek-reasoner"},
    })
    assert settings.ROUTING_LARGE_MODEL == "deepseek-reasoner"


def test_get_config_masks_key():
    """回显只给前 4 后 4——够认是哪一串，又不至于截图泄露。"""
    client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "zhipu", "api_key": "sk-abcdefgh12345678", "model": "glm-4.6",
    })
    d = client.get("/api/v1/models/config", headers=ADMIN).json()["data"]
    assert d["key_masked"].startswith("sk-a")
    assert d["key_masked"].endswith("5678")
    assert "abcdefgh1234" not in d["key_masked"]
    # 预设要带上，前端才能"换厂商自动填地址"
    ids = [p["id"] for p in d["presets"]]
    assert "zhipu" in ids and "custom" in ids


def test_new_model_is_registered_unpriced(_isolate_config):
    """配了一个注册表里没有的模型名 → 自动登记并标「未定价」，不在账单里凭空消失。"""
    r = client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "custom", "base_url": "https://llm.internal/v1",
        "api_key": "sk-x", "model": "qwen2.5-7b-instruct",
    })
    assert r.status_code == 200
    rows = client.get("/api/v1/models?status=all", headers=ADMIN).json()["data"]["models"]
    hit = next((m for m in rows if m["model_id"] == "qwen2.5-7b-instruct"), None)
    assert hit is not None
    assert hit.get("unpriced") is True
    assert hit["price_per_1k_prompt"] == 0.0


def test_persist_keeps_unrelated_lines(tmp_path):
    """落盘只覆盖本次涉及的键，注释与其它配置行原样保留（否则手工配置会被冲掉）。"""
    p = tmp_path / ".env.local"
    p.write_text("# 我的注释\nOTHER_KEY=keepme\nLLM_DEFAULT_MODEL=old\n", encoding="utf-8")
    runtime_config.persist({"LLM_DEFAULT_MODEL": "new-model"}, path=str(p))
    txt = p.read_text(encoding="utf-8")
    assert "# 我的注释" in txt
    assert "OTHER_KEY=keepme" in txt
    assert "LLM_DEFAULT_MODEL=new-model" in txt
    assert "old" not in txt
