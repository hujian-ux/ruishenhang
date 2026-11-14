"""P0-1 · 会话流式化（SSE + 中断）契约测试。

不依赖真模型：用 monkeypatch 把 agent_runtime.run_agent_stream / model_gateway.chat_stream_ex
换成确定性异步生成器，验证：
(1) /agent/stream 返回 text/event-stream 并依次推 meta/token/done/finalized，且会话已落账；
(2) DELETE /agent/stream/{id} 把 active 流的 cancelled 标志置真（白盒验证端点逻辑）；
(3) 真 run_agent_stream 在 should_cancel 置位后确实提前中止（不再产生 token/done），
    即「用户点取消 → 生成器停」整条链路生效；
(4) /agent/invoke 同步端点仍可用（回归）。

说明：FastAPI TestClient 会同步消费完整个流后才返回，无法在 HTTP 层构造「POST 尚未结束
就 DELETE」的时序；因此「取消」的端到端语义拆成 (2) 端点置位 + (3) 生成器尊取消两层验证，
二者合起来等价于真实取消链路。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.routers import invoke
from app.services import agent_runtime, agent_store, model_gateway, session_store
from app.tools import registry

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}


def _caller() -> CallerContext:
    return CallerContext(userId="p_admin", deptId="d_platform",
                        role="platform_admin", tenantId="")


@pytest.fixture(scope="module")
def seeded():
    before = set(agent_store._AGENTS)
    agent_store.ensure_public_skills()
    yield
    for aid in list(agent_store._AGENTS):
        if aid not in before:
            agent_store._AGENTS.pop(aid, None)


async def _fake_complete(agent_id, user_input, caller, history=None, system_prompt=None,
                        adapter_type="", file_context="", web_fetch_top=0, should_cancel=None, **kw):
    """确定性完整流：meta → 若干 token → done（携带完整 result）。"""
    yield {"type": "meta", "citations": [{"doc_id": "d1", "title": "T", "scope": "kb"}],
           "web_sources": [], "web_search": None, "tool_calls": [{"tool": "rag_retrieve"}],
           "query_rewritten": False}
    for i in range(3):
        yield {"type": "token", "text": f"句{i}。"}
    yield {"type": "done", "result": {
        "answer": "句0。句1。句2。", "citations": [{"doc_id": "d1", "title": "T", "scope": "kb"}],
        "web_sources": [], "web_search": None, "query_rewritten": False, "turn_no": 1,
        "usage": {"model_id": "glm-4.6", "real": True,
                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "error": "",
                  "latency_ms": 1}, "tool_calls": [{"tool": "rag_retrieve"}], "skill_applied": False}}


async def _fake_llm_stream(model_id, messages, timeout=0, max_tokens=None, temperature=None,
                          user_input="", agent=None, tenant_id=None, should_cancel=None):
    """模拟模型网关：发 5 个 delta，中途若被取消则不再发 end（与真 chat_stream_ex 一致）。"""
    for i in range(5):
        if should_cancel and should_cancel():
            return  # 取消：不 yield end，生成器直接结束
        yield {"t": "delta", "text": f"字{i}"}
    yield {"t": "end", "usage": {"prompt_tokens": 3, "completion_tokens": 5},
           "model_id": model_id or "glm-4.6", "real": True, "error": "",
           "routed_tier": None, "routed_model": None, "latency_ms": 1}


def test_stream_returns_sse_with_full_lifecycle(monkeypatch, seeded):
    monkeypatch.setattr(agent_runtime, "run_agent_stream", _fake_complete)
    r = client.post("/api/v1/agent/stream", headers=ADMIN,
                    json={"agent_id": "ag_pub_web_lookup", "input": "储能政策"})
    assert r.status_code == 200, r.text[:200]
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert r.headers.get("x-request-id", "").startswith("st_")
    events = [json.loads(ln[5:].strip()) for ln in r.text.splitlines() if ln.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "token" in types and "done" in types and "finalized" in types
    done = next(e for e in events if e["type"] == "done")
    assert done["result"]["answer"] == "句0。句1。句2。"
    # 记忆落账：会话已写入会话存储（通过会话列表端点验证，结构 {code,data:{sessions,count}}）
    r2 = client.get("/api/v1/agent/sessions", params={"all": "true"}, headers=ADMIN)
    assert r2.status_code == 200
    body = r2.json()["data"]
    assert isinstance(body["sessions"], list) and len(body["sessions"]) >= 1


def test_stream_cancel_endpoint_sets_flag(monkeypatch, seeded):
    """白盒：DELETE 端点对进行中的流把 cancelled 标志置真（不依赖 HTTP 并发时序）。"""
    monkeypatch.setattr(agent_runtime, "run_agent_stream", _fake_complete)
    rid = "st_whitebox_001"
    invoke.ACTIVE_STREAMS[rid] = {"cancelled": False, "caller_id": "p_admin"}
    d = client.delete(f"/api/v1/agent/stream/{rid}", headers=ADMIN)
    assert d.status_code == 200
    assert d.json()["data"]["cancelled"] is True
    assert invoke.ACTIVE_STREAMS[rid]["cancelled"] is True
    invoke.ACTIVE_STREAMS.pop(rid, None)


def test_stream_generator_honors_cancel(monkeypatch, seeded):
    """端到端：真 run_agent_stream 在 should_cancel 置位后提前中止（不再出 token/done）。"""
    monkeypatch.setattr(model_gateway, "chat_stream_ex", _fake_llm_stream)
    cancel = {"v": False}

    async def collect():
        evs = []
        async for ev in agent_runtime.run_agent_stream(
                "ag_pub_web_lookup", "储能政策", _caller(), should_cancel=lambda: cancel["v"]):
            evs.append(ev)
            if sum(1 for e in evs if e["type"] == "token") >= 2:
                cancel["v"] = True  # 收到 2 个 token 后取消
        return evs

    evs = asyncio.run(collect())
    types = [e["type"] for e in evs]
    assert types[0] == "meta"
    token_count = sum(1 for e in evs if e["type"] == "token")
    # 取消生效：第 3 个 delta 之前就因 cancelled 而中断，token 数应为 2，且不再产生 done
    assert token_count == 2, token_count
    assert "done" not in types


def test_stream_cancel_missing_request_returns_not_found(monkeypatch, seeded):
    monkeypatch.setattr(agent_runtime, "run_agent_stream", _fake_complete)
    d = client.delete("/api/v1/agent/stream/st_does_not_exist", headers=ADMIN)
    assert d.status_code == 200
    assert d.json()["data"]["cancelled"] is False


def test_invoke_still_works_after_stream_added(monkeypatch, seeded):
    """回归：新增流式端点不应影响原同步 /agent/invoke 契约。"""
    r = client.post("/api/v1/agent/invoke", headers=ADMIN,
                    json={"agent_id": "ag_pub_web_lookup", "input": "查一下储能容量补偿政策",
                          "stream": False})
    # 无真模型 + LLM_STRICT=1（测试环境）会 502；只验证端点存在且路径未被破坏
    assert r.status_code in (200, 502)


def test_stream_tools_branch_emits_tool_events(monkeypatch, seeded):
    """P0 收尾1：enable_tools=True 时工具循环走流式分支，SSE 依次推
    meta → token → tool_call → tool_result → token → done（不再把工具阶段压成单个 token）。"""
    def _ut(args, caller):
        return {"ok": True, "content": "产能=1200"}
    registry.register("ut_stream_cap", "产能查询", {"item": "编码"}, _ut)
    script = [
        '查询：```tool_call\n{"tool":"ut_stream_cap","args":{"item":"A1"}}\n```',
        '该 SKU 产能为 1200。',
    ]

    def factory(script):
        state = {"i": 0}

        async def gen(model_id, messages, **kw):
            i = state["i"]
            state["i"] += 1
            text = script[i] if i < len(script) else script[-1]
            yield {"t": "delta", "text": text}
            yield {"t": "end", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                   "real": True, "model_id": "m", "routed_tier": None, "routed_model": None,
                   "latency_ms": 1, "error": "", "throttled": False}

        return gen

    monkeypatch.setattr(model_gateway, "chat_stream_ex", factory(script))

    async def collect():
        evs = []
        async for ev in agent_runtime.run_agent_stream(
                "ag_pub_web_lookup", "查A1产能", _caller(), enable_tools=True):
            evs.append(ev)
        return evs

    evs = asyncio.run(collect())
    types = [e["type"] for e in evs]
    assert types[0] == "meta"
    assert "tool_call" in types
    assert "tool_result" in types
    assert "done" in types
    tc = next(e for e in evs if e["type"] == "tool_call")
    assert tc["tool"] == "ut_stream_cap"
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["ok"] is True
    assert "1200" in tr["content"]
    done = next(e for e in evs if e["type"] == "done")
    assert "产能为 1200" in done["result"]["answer"]
    # 高危转审批分支的回灌依赖 payload.session_id；这里确认非高危路径不受影响
    assert done["result"]["approval_required"] is False
