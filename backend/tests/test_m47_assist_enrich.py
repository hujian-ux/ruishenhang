"""M47 · 审批助手 enrich 接真模型通道：enrich=true 透传、真回复才有 llm_hint、失败优雅降级。"""
import itertools
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import model_gateway

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_a47e", "d_it", "platform_admin", "t_demo")}


_SEQ = itertools.count(1)


def _register():
    emp = {"Authorization": create_dev_token("u_emp47e", "d_sales", "employee", "t_demo")}
    # M53：agent_id 由「租户+名字」确定性生成，同名注册会 409（防静默覆盖）。
    # 每个用例注册自己的智能体，名字必须唯一。
    r = client.post("/api/v1/agents",
                    json={"name": f"M47助手enrich{next(_SEQ)}", "adapter_type": "llm_retrieval",
                          "description": "M47", "knowledge_scope": "department"}, headers=emp)
    assert r.status_code == 200, r.text
    return r.json()["data"]["approval_id"]


def test_enrich_true_propagates_llm_hint(monkeypatch):
    apr_id = _register()
    monkeypatch.setattr(
        model_gateway, "chat_sync_ex",
        lambda *a, **k: {"text": "重点提示：该智能体为全局知识域，需谨慎。", "real": True,
                          "model_id": "glm-4.6", "usage": {}, "error": ""})
    r = client.post(f"/api/v1/approval/{apr_id}/assist?enrich=true", headers=_admin())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["llm_hint"] == "重点提示：该智能体为全局知识域，需谨慎。"
    # 结构化结论不受影响
    assert "summary" in d and "draft" in d and "不落账" in d["note"]


def test_enrich_without_real_reply_has_no_llm_hint(monkeypatch):
    apr_id = _register()
    # 真模型不可用（real=False，例如未配置密钥走 stub）：不出 llm_hint，不影响主结论
    monkeypatch.setattr(
        model_gateway, "chat_sync_ex",
        lambda *a, **k: {"text": "stub", "real": False, "model_id": "x", "usage": {},
                          "error": "未配置密钥"})
    r = client.post(f"/api/v1/approval/{apr_id}/assist?enrich=true", headers=_admin())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert "llm_hint" not in d
    assert "summary" in d


def test_enrich_false_default_has_no_llm_hint():
    apr_id = _register()
    r = client.post(f"/api/v1/approval/{apr_id}/assist", headers=_admin())
    assert r.status_code == 200, r.text
    assert "llm_hint" not in r.json()["data"]
