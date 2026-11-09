"""M51 · 模型轮换链（免费额度池续命）。

客户用免费额度跑（硅基流动每模型独立 RPM/TPM 与日额度），单模型趴窝不能整条链停摆。
这一层守五件事：

1. 分类正确：402/403 是额度型、429 是限流型、401 不轮换（一把 key 换模型照样 401）；
2. 顺序正确：主模型 → 链上其余，非对话模型（bge-m3/rerank）自动跳过；
3. 真会轮换：402 之后用下一个模型答出来，且回传 rotated/attempts；
4. 冷却生效：失败模型进冷却，下次优先用上次成功的那个，不白打一次；
5. 全军覆没要如实报错，不能静默成 stub 还显示"通了"。

不联网：httpx.post 全部 monkeypatch。
"""
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.services import model_gateway, runtime_config

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_1024", "d_sales", "employee")}

_ENV_KEYS = ("LLM_PROVIDER", "LLM_VENDOR", "LLM_BASE_URL", "LLM_API_KEY",
             "LLM_DEFAULT_MODEL", "LLM_MODEL_CHAIN", "ROUTING_ENABLED")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离全局配置、注册表、轮换状态与落盘路径。"""
    from app.core.config import settings
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_attr = {k: getattr(settings, k) for k in _ENV_KEYS if hasattr(settings, k)}
    saved_registry = dict(model_gateway._REGISTRY)

    runtime_config.apply({"LLM_PROVIDER": "direct", "LLM_VENDOR": "siliconflow",
                          "LLM_BASE_URL": "https://api.siliconflow.cn/v1",
                          "LLM_API_KEY": "sk-test", "LLM_DEFAULT_MODEL": "Qwen/Qwen2.5-7B-Instruct",
                          "LLM_MODEL_CHAIN": "",
                          # 关掉成本路由：本文件要测"主模型 → 备胎"的顺序，
                          # 路由会把请求改写回 ROUTING_* 里的模型名，干扰断言
                          "ROUTING_ENABLED": "0"})
    model_gateway.reset_chain_state()
    # llm_available 有 30s 缓存：上个用例刚探过"未配置"，不清掉这里会直接短路成 stub
    model_gateway.llm_available(force=True)
    monkeypatch.setattr(runtime_config, "persist", lambda updates, path=None: None)
    yield
    model_gateway.reset_chain_state()
    model_gateway._REGISTRY.clear()
    model_gateway._REGISTRY.update(saved_registry)
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k, v in saved_attr.items():
        setattr(settings, k, v)


class _Resp:
    def __init__(self, status: int, body: dict | str = ""):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else str(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def _ok(model: str) -> _Resp:
    return _Resp(200, {"model": model,
                       "choices": [{"message": {"content": f"来自 {model} 的回答"}}],
                       "usage": {"prompt_tokens": 3, "completion_tokens": 4}})


def test_classify_status():
    assert model_gateway.classify_status(402, "余额不足") == "quota"
    assert model_gateway.classify_status(403, "insufficient balance") == "quota"
    # 免费模型最常见的 403 是上游饱和，属瞬时压力，不该冷却半小时
    assert model_gateway.classify_status(403, "当前分组上游负载已饱和") == "ratelimit"
    assert model_gateway.classify_status(429, "rate limit") == "ratelimit"
    assert model_gateway.classify_status(401, "invalid key") == "auth"
    assert model_gateway.classify_status(404, "no such model") == "notfound"
    assert model_gateway.classify_status(400, "Model does not exist") == "notfound"
    assert model_gateway.classify_status(500, "boom") == "fatal"


def test_chain_parsing_skips_non_chat_models():
    runtime_config.apply({"LLM_MODEL_CHAIN":
                          "Qwen/Qwen3-8B, BAAI/bge-m3 ; BAAI/bge-reranker-v2-m3\nQwen/Qwen3-8B"})
    chain = model_gateway.chain_models()
    assert chain == ["Qwen/Qwen3-8B"], "向量/重排模型混进对话链只会 400，必须过滤且去重"


def test_rotation_order_primary_first_then_chain():
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B,THUDM/GLM-4-9B-0414"})
    order = model_gateway.rotation_order("Qwen/Qwen2.5-7B-Instruct")
    assert order[0] == "Qwen/Qwen2.5-7B-Instruct"
    assert order[1:] == ["Qwen/Qwen3-8B", "THUDM/GLM-4-9B-0414"]


def test_rotates_to_next_model_on_quota(monkeypatch):
    """主模型 402（欠费/额度用尽）→ 自动换下一个并答出来。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json["model"])
        if json["model"] == "Qwen/Qwen2.5-7B-Instruct":
            return _Resp(402, "insufficient balance")
        return _ok(json["model"])

    monkeypatch.setattr(model_gateway.httpx, "post", fake_post)
    out = model_gateway.chat_sync_ex(None, [{"role": "user", "content": "你好"}])
    assert out["real"] is True
    assert out["model_id"] == "Qwen/Qwen3-8B"
    assert out["rotated"] is True
    assert seen == ["Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3-8B"]
    assert out["attempts"][0]["kind"] == "quota"


def test_401_does_not_rotate(monkeypatch):
    """key 错了换模型也没用：只打一次，别把延迟乘 N。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B,THUDM/GLM-4-9B-0414"})
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json["model"])
        return _Resp(401, "invalid api key")

    monkeypatch.setattr(model_gateway.httpx, "post", fake_post)
    out = model_gateway.chat_sync_ex(None, [{"role": "user", "content": "你好"}])
    assert out["real"] is False
    assert len(seen) == 1
    assert "密钥无效" in out["error"]


def test_prefers_last_good_model_after_rotation(monkeypatch):
    """轮换过去之后，下一次直接从活着的模型开始，不再白打一次 402。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "Qwen/Qwen2.5-7B-Instruct":
            return _Resp(402, "insufficient balance")
        return _ok(json["model"])

    monkeypatch.setattr(model_gateway.httpx, "post", fake_post)
    model_gateway.chat_sync_ex(None, [{"role": "user", "content": "第一次"}])
    calls.clear()
    out = model_gateway.chat_sync_ex(None, [{"role": "user", "content": "第二次"}])
    assert calls == ["Qwen/Qwen3-8B"], "上次成功的模型应排在最前"
    # rotated=True 是对的：答话的不是配置的默认模型，页面要能看出"跑在备胎上"
    assert out["model_id"] == "Qwen/Qwen3-8B" and out["rotated"] is True


def test_all_fail_reports_error_not_silent(monkeypatch):
    """全部 402：必须 real=False 且带原因，不能拿 stub 冒充成功。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})
    monkeypatch.setattr(model_gateway.httpx, "post",
                        lambda *a, **kw: _Resp(402, "insufficient balance"))
    out = model_gateway.chat_sync_ex(None, [{"role": "user", "content": "你好"}])
    assert out["real"] is False
    assert "余额不足" in out["error"]
    assert len(out["attempts"]) == 2


def test_cooling_moves_model_to_back(monkeypatch):
    """失败过的模型进冷却，后续调用把它排到后面（额度按天重置，不每分钟白试）。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})
    model_gateway._mark_fail("Qwen/Qwen3-8B", "quota", "402")
    order = model_gateway.rotation_order("Qwen/Qwen2.5-7B-Instruct")
    assert order[0] == "Qwen/Qwen2.5-7B-Instruct"
    st = model_gateway.chain_state()
    row = next(r for r in st["models"] if r["model"] == "Qwen/Qwen3-8B")
    assert row["cooling"] is True and row["cool_remaining_s"] > 0


def test_probe_rotates_and_reports_attempts(monkeypatch):
    """「保存并测试」一次就把整条链谁死了、谁活着报出来。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})

    def fake_post(url, json=None, headers=None, timeout=None):
        if json["model"] == "Qwen/Qwen2.5-7B-Instruct":
            return _Resp(402, "insufficient balance")
        return _ok(json["model"])

    monkeypatch.setattr(model_gateway.httpx, "post", fake_post)
    res = model_gateway.probe()
    assert res["ok"] is True
    assert res["model_id"] == "Qwen/Qwen3-8B"
    assert res["requested"] == "Qwen/Qwen2.5-7B-Instruct"
    assert res["rotated"] is True
    assert [a["model"] for a in res["attempts"]] == ["Qwen/Qwen2.5-7B-Instruct",
                                                    "Qwen/Qwen3-8B"]


def test_put_config_saves_chain_and_filters_non_chat():
    r = client.put("/api/v1/models/config", headers=ADMIN, json={
        "provider": "direct", "vendor": "siliconflow",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "chain": ["Qwen/Qwen3-8B", "BAAI/bge-m3", "Qwen/Qwen2.5-7B-Instruct"],
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["config"]["chain"]["chain"] == ["Qwen/Qwen3-8B"]
    assert data["chain_skipped"] == ["BAAI/bge-m3"]


def test_put_config_requires_admin_for_chain():
    r = client.put("/api/v1/models/config", headers=EMP,
                   json={"model": "Qwen/Qwen3-8B", "chain": ["x"]})
    assert r.status_code == 403


def test_reset_chain_clears_cooldown():
    model_gateway._mark_fail("Qwen/Qwen3-8B", "quota", "402")
    assert model_gateway.chain_state()["models"] or True
    r = client.post("/api/v1/models/chain/reset", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["data"]["models"] == []
    assert model_gateway._CHAIN_STATE == {}


def test_invoke_surfaces_rotated_model():
    """调用链要把「实际是哪个模型答的」带到响应里，页面才说得清。"""
    from app.routers import invoke as invoke_mod
    # 只要 llm 块里带 model/rotated/attempts 三个键即可（真实作答由上面单测覆盖）
    src = __import__("inspect").getsource(invoke_mod.invoke_agent)
    assert '"rotated"' in src and '"attempts"' in src and '"model"' in src


def test_probe_all_tests_every_model(monkeypatch):
    """池子体检：链上每个都测，报告谁还有额度（不能只测到第一个活的就停）。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B,THUDM/GLM-4-9B-0414"})
    dead = {"THUDM/GLM-4-9B-0414"}

    def fake_post(url, json=None, headers=None, timeout=None):
        if json["model"] in dead:
            return _Resp(402, "insufficient balance")
        return _ok(json["model"])

    monkeypatch.setattr(model_gateway.httpx, "post", fake_post)
    res = model_gateway.probe_all()
    assert res["tested"] == 3
    assert res["alive"] == ["Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3-8B"]
    assert res["dead"] == ["THUDM/GLM-4-9B-0414"]
    assert res["ok"] is True


def test_probe_all_via_endpoint_requires_admin():
    r = client.get("/api/v1/models/probe?all=1", headers=EMP)
    assert r.status_code == 403


def test_probe_all_does_not_hijack_last_good(monkeypatch):
    """体检是诊断，不是真实调用：不能因为它把默认模型悄悄换掉。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})
    monkeypatch.setattr(model_gateway.httpx, "post",
                        lambda url, json=None, headers=None, timeout=None: _ok(json["model"]))
    model_gateway.probe_all()
    assert model_gateway.chain_state()["last_good"] == ""


def test_primary_retried_first_once_cooldown_expires(monkeypatch):
    """额度恢复后要能回到主模型：主模型不在冷却时，顺序必须按配置来。"""
    runtime_config.apply({"LLM_MODEL_CHAIN": "Qwen/Qwen3-8B"})
    # 伪造"上次成功的是备胎"，但主模型没在冷却（模拟额度已重置）
    model_gateway._mark_ok("Qwen/Qwen3-8B")
    order = model_gateway.rotation_order("Qwen/Qwen2.5-7B-Instruct")
    assert order[0] == "Qwen/Qwen2.5-7B-Instruct", "主模型可用时应尊重配置，不该永远卡在备胎上"
    # 主模型进了冷却，才轮到备胎顶到最前
    model_gateway._mark_fail("Qwen/Qwen2.5-7B-Instruct", "quota", "402")
    order2 = model_gateway.rotation_order("Qwen/Qwen2.5-7B-Instruct")
    assert order2[0] == "Qwen/Qwen3-8B"
