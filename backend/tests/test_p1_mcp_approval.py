"""P1-3 MCP 工具审批 + 沙箱代理（#298）回归测试。

三层闸：
  1) 沙箱硬闸：仅 MCP_TOOL_ALLOW 显式声明的端点放行（mcp_endpoint_not_declared 拒绝），
     且凭据三态硬禁仍对 MCP 入参生效；
  2) 声明 requires_approval=True 的 MCP 工具在工具循环里挂起为 tool_execution 审批单；
  3) 审批通过后由 mcp_client.call_mcp_tool 单点执行（路由按 mcp__ 前缀分发），
     驳回不执行；免审工具（requires_approval=False 且端点已声明）经护栏授权后直执行。

不依赖真模型 / 真 MCP Server：client 管理器直接注入假工具列表，call_mcp_tool 以
monkeypatch 替代；工具循环用脚本化 get_llm 驱动。
"""
import pytest

from app.models.caller import CallerContext
from app.tools import sandbox, loop as tool_loop
from app.services import approval_store, session_store

CALLER = CallerContext(userId="u1", deptId="d1", role="employee", tenantId="t1")


@pytest.fixture(autouse=True)
def _reset_mcp_manager():
    """隔离 client 管理器单例，避免跨测试 / 跨文件污染。"""
    from app.services import mcp_client
    mgr = mcp_client._manager
    saved_sessions = mgr._sessions
    saved_servers = mgr._servers
    saved_connected = mgr._connected
    yield
    mgr._sessions = saved_sessions
    mgr._servers = saved_servers
    mgr._connected = saved_connected


# ───────────────────────── #316 mcp_client 元数据 ─────────────────────────
def test_mcp_requires_approval_metadata():
    from app.services import mcp_client
    mcp_client._manager.inject_session(
        "srv", None,
        [{"name": "ro", "description": "", "input_schema": {},
          "annotations": {"readOnlyHint": True}, "requires_approval": False},
         {"name": "rw", "description": "", "input_schema": {},
          "annotations": {"destructiveHint": True}, "requires_approval": True}])
    tools = mcp_client.list_mcp_tools()
    assert len(tools) == 2
    by_name = {t["name"]: t for t in tools}
    assert by_name["ro"]["requires_approval"] is False
    assert by_name["rw"]["requires_approval"] is True
    # 同时暴露 annotations 与 server 级开关
    assert by_name["ro"]["annotations"]["readOnlyHint"] is True
    assert by_name["rw"]["server_requires_approval"] is True

    # requires_approval 判定（fail-closed 默认 True）
    assert mcp_client.mcp_requires_approval("mcp__srv__ro") is False
    assert mcp_client.mcp_requires_approval("mcp__srv__rw") is True
    assert mcp_client.mcp_requires_approval("mcp__srv__ghost") is True
    assert mcp_client.mcp_requires_approval("mcp__nope__x") is True
    assert mcp_client.mcp_requires_approval("not_mcp_tool") is True


# ───────────────────────── #318 沙箱硬闸 ─────────────────────────
def test_sandbox_mcp_endpoint_not_declared(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "")
    r = sandbox.check("mcp__evil__drop", {"anything": 1}, rules=sandbox.load_rules())
    assert r["allowed"] is False
    assert r["reason"] == "mcp_endpoint_not_declared"


def test_sandbox_mcp_endpoint_declared_passes(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "mcp__ok__*")
    r = sandbox.check("mcp__ok__read", {}, rules=sandbox.load_rules())
    assert r["allowed"] is True
    assert r["reason"] == "delegated_to_guardrails"


def test_sandbox_mcp_secret_still_forbidden(monkeypatch):
    """凭据三态硬禁对 MCP 入参同样生效（即便端点已声明）。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "mcp__ok__*")
    r = sandbox.check("mcp__ok__x", {"token": "sk-1234567890abcdef"},
                      rules=sandbox.load_rules())
    assert r["allowed"] is False
    assert "secret_forbidden" in r["reason"]


# ───────────────────────── #319 工具循环：挂起审批 ─────────────────────────
def _fake_llm_scripted(script):
    state = {"i": 0}

    def fn(messages):
        i = state["i"]
        state["i"] += 1
        text = script[i] if i < len(script) else script[-1]
        return {"text": text, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return fn


def test_loop_mcp_requires_approval_suspends(monkeypatch):
    """声明需审批的 MCP 工具在工具循环里挂起为 tool_execution 单，不执行。"""
    from app.services import mcp_client
    from app.core.config import settings
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "mcp__ext__*")
    mcp_client._manager.inject_session(
        "ext", None,
        [{"name": "search", "description": "", "input_schema": {},
          "annotations": {}, "requires_approval": True}])
    script = ['查：```tool_call\n{"tool":"mcp__ext__search","args":{"q":"天气"}}\n```']
    res = tool_loop.run_tool_loop("查天气", "sys", CALLER, _fake_llm_scripted(script),
                                  max_rounds=6, agent_id="agent_mcp")
    assert res["approval_required"] is True
    assert res["approval"]["tool"] == "mcp__ext__search"
    assert res["approval"]["ticket_id"]
    assert res["tool_calls"][0]["reason"] == "mcp_tool_escalated_to_approval"
    t = approval_store.get_ticket(res["approval"]["ticket_id"])
    assert t["ticket_type"] == "tool_execution"
    assert t["payload"]["tool"] == "mcp__ext__search"
    assert t["payload"]["args"]["q"] == "天气"
    assert t["tenant_id"] == "t1"
    assert t["status"] == "pending"


def test_loop_stream_mcp_requires_approval_suspends(monkeypatch):
    """流式工具循环：MCP 需审批工具发 tool_escalated 事件并落单。"""
    import asyncio
    from app.services import mcp_client
    from app.core.config import settings

    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "mcp__ext__*")
    mcp_client._manager.inject_session(
        "ext", None,
        [{"name": "search", "description": "", "input_schema": {},
          "annotations": {}, "requires_approval": True}])

    def _scripted_stream_factory(script):
        state = {"i": 0}

        async def gen(messages):
            i = state["i"]
            state["i"] += 1
            text = script[i] if i < len(script) else script[-1]
            yield {"t": "delta", "text": text}
            yield {"t": "end", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                   "real": True, "model_id": "m", "routed_tier": None, "routed_model": None,
                   "latency_ms": 1, "error": "", "throttled": False}
        return gen

    script = ['```tool_call\n{"tool":"mcp__ext__search","args":{"q":"x"}}\n```']
    evs = []
    async def _collect():
        async for ev in tool_loop.run_tool_loop_stream(
                "查x", "sys", CALLER, _scripted_stream_factory(script),
                max_rounds=6, agent_id="agent_mcp", session_id="sess_mcp"):
            evs.append(ev)
    asyncio.run(_collect())
    types = [e["type"] for e in evs]
    assert "tool_escalated" in types
    esc = next(e for e in evs if e["type"] == "tool_escalated")
    assert esc["ticket_id"]
    t = approval_store.get_ticket(esc["ticket_id"])
    assert t["payload"]["session_id"] == "sess_mcp"


def test_loop_mcp_no_approval_executes(monkeypatch):
    """免审 MCP 工具（端点已声明 + requires_approval=False）经护栏授权后直执行。"""
    from app.services import mcp_client
    from app.core.config import settings
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "mcp__ext__*")
    monkeypatch.setattr(mcp_client, "call_mcp_tool",
                        lambda qualified, args: {"content": "晴 26℃", "is_error": False})
    mcp_client._manager.inject_session(
        "ext", None,
        [{"name": "weather", "description": "", "input_schema": {},
          "annotations": {"readOnlyHint": True}, "requires_approval": False}])
    script = [
        '查：```tool_call\n{"tool":"mcp__ext__weather","args":{"q":"东莞"}}\n```',
        '东莞今天晴，26℃。',
    ]
    res = tool_loop.run_tool_loop("查东莞天气", "sys", CALLER, _fake_llm_scripted(script),
                                  max_rounds=6)
    assert res["approval_required"] is False
    assert res["tool_calls"][0]["tool"] == "mcp__ext__weather"
    assert res["tool_calls"][0]["allowed"] is True
    assert "晴" in res["tool_calls"][0]["content"]
    assert "东莞今天晴" in res["answer"]


def test_loop_mcp_endpoint_undeclared_rejected_by_sandbox(monkeypatch):
    """未声明端点的 MCP 工具在沙箱层即被拒，不会进入审批 / 执行。"""
    from app.services import mcp_client
    from app.core.config import settings
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "")  # 未声明
    mcp_client._manager.inject_session(
        "ext", None,
        [{"name": "weather", "description": "", "input_schema": {},
          "annotations": {}, "requires_approval": False}])
    script = ['查：```tool_call\n{"tool":"mcp__ext__weather","args":{"q":"x"}}\n```',
              '无法查询。']
    res = tool_loop.run_tool_loop("查x", "sys", CALLER, _fake_llm_scripted(script),
                                  max_rounds=6)
    assert res["approval_required"] is False
    assert res["tool_calls"][0]["allowed"] is False
    assert "mcp_endpoint_not_declared" in res["tool_calls"][0]["reason"]
    assert "无法查询" in res["answer"]


# ───────────────────────── #320 审批路由：MCP 工具执行 ─────────────────────────
def test_approval_mcp_execution_on_approve(monkeypatch):
    """MCP 工具审批通过 → 由 mcp_client 单点执行并回灌会话；驳回不执行。"""
    from app.services import mcp_client
    from app.core.security import create_dev_token
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(mcp_client, "call_mcp_tool",
                        lambda qualified, args: {"content": "MCP结果: ok", "is_error": False})
    client = TestClient(app)
    ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
    caller = CallerContext(userId="p_admin", deptId="d_platform", role="platform_admin", tenantId="")
    sess = session_store.get_or_create("ag_pub_web_lookup", caller, None)
    sid = sess["session_id"]

    ticket = approval_store.create_tool_approval_ticket(
        caller, "ag_pub_web_lookup", "mcp__ext__search", {"q": "x"}, session_id=sid)
    tid = ticket["approval_id"]

    r = client.post(f"/api/v1/approval/{tid}/decide", headers=ADMIN, json={"decision": "approve"})
    assert r.status_code == 200, r.text[:200]
    body = r.json()["data"]
    assert body["status"] == "approved"
    assert body["execution"]["ok"] is True
    assert "MCP结果" in body["execution"]["content"]

    turns = session_store.history_of(sid)
    assert any("已审批执行" in t["content"] for t in turns)
    assert any("MCP结果" in t["content"] for t in turns)


def test_approval_mcp_reject_not_executed(monkeypatch):
    """MCP 工具审批驳回 → 不执行（call_mcp_tool 不应被调用）。"""
    from app.services import mcp_client
    from app.core.security import create_dev_token
    from app.main import app
    from fastapi.testclient import TestClient

    called = {"n": 0}

    def fake_call(qualified, args):
        called["n"] += 1
        return {"content": "should-not-run", "is_error": False}

    monkeypatch.setattr(mcp_client, "call_mcp_tool", fake_call)
    client = TestClient(app)
    ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
    caller = CallerContext(userId="p_admin", deptId="d_platform", role="platform_admin", tenantId="")
    ticket = approval_store.create_tool_approval_ticket(
        caller, "ag_pub_web_lookup", "mcp__ext__search", {"q": "x"})
    tid = ticket["approval_id"]

    r = client.post(f"/api/v1/approval/{tid}/decide", headers=ADMIN, json={"decision": "reject"})
    assert r.status_code == 200, r.text[:200]
    body = r.json()["data"]
    assert body["status"] == "rejected"
    assert body["execution"] is None
    assert called["n"] == 0  # 驳回不执行
