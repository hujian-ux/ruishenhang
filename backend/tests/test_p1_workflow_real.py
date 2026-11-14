"""P1-6 · 工作流真执行。

验证节点式工作流从 dry_run 升级为真实执行：
- llm 节点真实调用模型网关（routed_chat_sync_ex），dry_run=False，产出真实文本；
- 前序节点输出（rag/llm/tool）贯穿后续 llm 节点的上下文；
- 可选 agent_id 绑定智能体人设作为 llm 节点的 system 角色；
- approval 节点仍停下等人工，不自动跳过后续节点；
- /execute-stream 以 SSE 下发 meta/node_start/node_result/done；
- 跨租户执行工作流被 403 拦截（H3 红线）；
- 既有 dry_run 的 /run 端点行为不变（回归）。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware
from app.core.security import create_dev_token
from app.routers import builder
from app.services import agent_store, audit, model_gateway, workflows

app = FastAPI()
app.add_middleware(AuditTraceMiddleware)
app.add_middleware(GatewayMiddleware)
app.include_router(builder.router, prefix="/api/v1")

client = TestClient(app)
EMP_A = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee", "t_a")}
EMP_B = {"Authorization": create_dev_token("u_li", "d_hr", "employee", "t_b")}


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    monkeypatch.setattr(audit, "record", lambda *a, **k: "trace")
    workflows.reset_state()
    yield
    workflows.reset_state()


def _new_workflow(nodes, name="流"):
    return client.post("/api/v1/builder/workflows",
                       json={"name": name, "nodes": nodes}, headers=EMP_A).json()["data"]


def _patch_llm(monkeypatch, recorded):
    def fake(messages, user_input="", agent=None, **kw):
        recorded.append({"messages": messages, "agent": agent, "user_input": user_input})
        user = messages[-1]["content"] if messages else ""
        return {"text": f"GEN<<{user_input}>>", "real": True, "model_id": "fake-model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 6}, "error": "",
                "rotated": False}

    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex", fake)


def test_execute_real_not_dry_run(monkeypatch):
    rec: list = []
    _patch_llm(monkeypatch, rec)
    wf = _new_workflow([{"type": "llm", "name": "起草", "config": {"instruction": "写摘要"}}])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute",
                    json={"input": "测试输入"}, headers=EMP_A).json()["data"]
    assert d["dry_run"] is False
    assert d["status"] == "completed"
    step = d["steps"][0]
    assert step["type"] == "llm" and step["ok"] is True
    assert step["output"]["mode"] == "executed"
    assert step["output"]["real"] is True
    assert d["answer"] == "GEN<<测试输入>>"
    assert d["usage"]["calls"] == 1
    assert d["usage"]["prompt_tokens"] == 12


def test_execute_context_threading(monkeypatch):
    rec: list = []
    _patch_llm(monkeypatch, rec)
    wf = _new_workflow([
        {"type": "llm", "name": "起草", "config": {"instruction": "第一步"}},
        {"type": "llm", "name": "润色", "config": {"instruction": "第二步"}},
    ])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute",
                    json={"input": "X"}, headers=EMP_A).json()["data"]
    assert len(rec) == 2
    # 第二个 llm 节点的 user prompt 应包含第一个节点生成的文本（上下文贯穿）
    second_user = rec[1]["messages"][-1]["content"]
    assert "GEN<<X>>" in second_user
    assert "第一步" in rec[0]["messages"][-1]["content"]
    assert d["answer"] == "GEN<<X>>"  # 最终答案取最后一个 llm 节点


def test_execute_rag_feeds_llm(monkeypatch):
    rec: list = []
    _patch_llm(monkeypatch, rec)
    from app.services import knowledge

    def fake_retrieve(query, caller, top_k=6):
        return ([{"snippet": "片段A内容", "title": "文档1"}], 0)

    monkeypatch.setattr(knowledge, "retrieve", fake_retrieve)
    wf = _new_workflow([
        {"type": "rag", "name": "查知识", "config": {"query": "报销"}},
        {"type": "llm", "name": "生成", "config": {"instruction": "基于检索写"}},
    ])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute",
                    json={"input": "I"}, headers=EMP_A).json()["data"]
    assert d["steps"][0]["type"] == "rag" and d["steps"][0]["ok"] is True
    llm_user = rec[0]["messages"][-1]["content"]
    assert "片段A内容" in llm_user               # rag 片段进入 llm 上下文
    assert "rag:查知识" in d["context_keys"]


def test_execute_agent_binding_persona(monkeypatch):
    rec: list = []
    _patch_llm(monkeypatch, rec)
    aid = "ag_bind_001"
    agent_store._AGENTS[aid] = {"agent_id": aid, "tenant_id": "t_a",
                                "system_prompt": "人设XYZ"}
    try:
        wf = _new_workflow([{"type": "llm", "name": "生成", "config": {"instruction": "执行"}}])
        d = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute",
                        json={"input": "I", "agent_id": aid}, headers=EMP_A).json()["data"]
        assert d["agent_id"] == aid
        assert rec[0]["messages"][0]["content"] == "人设XYZ"   # system 即人设
        assert rec[0]["agent"]["system_prompt"] == "人设XYZ"    # agent 参数透传
    finally:
        agent_store._AGENTS.pop(aid, None)


def test_execute_approval_stops_real(monkeypatch):
    rec: list = []
    _patch_llm(monkeypatch, rec)
    wf = _new_workflow([
        {"type": "llm", "name": "整理", "config": {}},
        {"type": "approval", "name": "人工审", "config": {}},
        {"type": "llm", "name": "生成", "config": {}},
    ])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute",
                    json={"input": "资料"}, headers=EMP_A).json()["data"]
    assert d["status"] == "pending"
    assert [s["type"] for s in d["steps"]] == ["llm", "approval"]  # 第三个 llm 被截停
    assert "llm:生成" not in d["context_keys"]
    assert len(rec) == 1                                        # 只调一次模型


def test_execute_stream_sse(monkeypatch):
    rec: list = []
    _patch_llm(monkeypatch, rec)
    wf = _new_workflow([{"type": "llm", "name": "起草", "config": {"instruction": "写"}}])
    resp = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute-stream",
                       json={"input": "S"}, headers=EMP_A)
    assert resp.status_code == 200
    events: list = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:].strip()))
    types = [e["type"] for e in events]
    assert "meta" in types and "node_start" in types and "node_result" in types and "done" in types
    done = [e for e in events if e["type"] == "done"][0]
    assert done["dry_run"] is False
    assert done["answer"] == "GEN<<S>>"


def test_execute_cross_tenant_forbidden(monkeypatch):
    _patch_llm(monkeypatch, [])
    wf = _new_workflow([{"type": "llm", "name": "x", "config": {}}])  # 归属 t_a（EMP_A）
    r = client.post(f"/api/v1/builder/workflows/{wf['id']}/execute",
                    json={"input": "z"}, headers=EMP_B)              # t_b 执行
    assert r.status_code == 403


def test_run_still_dry_run_regression(monkeypatch):
    wf = _new_workflow([{"type": "llm", "name": "x", "config": {}}])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": "z"}, headers=EMP_A).json()["data"]
    assert d["dry_run"] is True
    assert d["steps"][0]["output"]["mode"] == "dry_run"
