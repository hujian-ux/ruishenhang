"""MCP 方向2（平台消费外部工具）测试。

覆盖：
- 配置解析（数组 / 对象 / NDJSON）
- 注入假会话后的 list_tools / call_tool（无需真实网络）
- guardrails 对 mcp__ 工具的显式授权门（默认全拒）
- 端到端：启动平台自带的 mcp_stdio_server.py 作为「外部 Server」，经 stdio 真实连接并枚举工具
"""
import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import mcp_client
from app.services import guardrails


@pytest.fixture(autouse=True)
def _reset_singleton():
    yield
    # 清理单例会话/连接状态，避免跨测试泄漏（guardrails 测试用到全局 _manager）。
    mcp_client._manager._sessions = {}
    mcp_client._manager._connected = False


def _fake_session(tools):
    ns_tools = [
        SimpleNamespace(name=t["name"], description=t.get("description", ""),
                        inputSchema=t.get("input_schema", {}))
        for t in tools
    ]

    class Fake:
        async def list_tools(self):
            return SimpleNamespace(tools=ns_tools)

        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text",
                                        text=f"result:{name}:{json.dumps(arguments or {})})")],
                isError=False,
            )

    return Fake()


def _inject(manager, name, tools):
    manager.inject_session(name, _fake_session(tools), tools)


def test_config_parse_list():
    m = mcp_client.ExternalMCPManager()
    m.load_from_env(json.dumps([
        {"name": "a", "transport": "stdio", "command": "x"},
        {"name": "b", "transport": "http", "url": "http://b"},
    ]))
    assert set(m._servers) == {"a", "b"}
    assert m._servers["b"].transport == "http"


def test_config_parse_dict_and_ndjson():
    m = mcp_client.ExternalMCPManager()
    m.load_from_env(json.dumps({"s1": {"transport": "sse", "url": "http://s1"}}))
    assert m._servers["s1"].transport == "sse"

    m2 = mcp_client.ExternalMCPManager()
    m2.load_from_env('\n'.join([
        '{"name":"n1","transport":"stdio","command":"c1"}',
        '{"name":"n2","transport":"http","url":"http://n2"}',
    ]))
    assert set(m2._servers) == {"n1", "n2"}


def test_list_and_call_via_injected_session():
    m = mcp_client.ExternalMCPManager()
    _inject(m, "demo", [
        {"name": "search", "description": "搜索", "input_schema": {"type": "object"}},
        {"name": "calc", "description": "计算", "input_schema": {"type": "object"}},
    ])
    tools = m.list_tools()
    assert {t["qualified"] for t in tools} == {
        "mcp__demo__search", "mcp__demo__calc"}
    res = asyncio.run(m.call_tool("demo", "search", {"q": "hi"}))
    assert res["is_error"] is False
    assert "result:search" in res["content"]


def test_split_qualified():
    assert mcp_client.split_qualified("mcp__srv__tool") == ("srv", "tool")
    with pytest.raises(ValueError):
        mcp_client.split_qualified("not_mcp__x")


def test_guardrails_mcp_denied_by_default(monkeypatch):
    monkeypatch.setattr(settings, "GUARDRAILS_TOOL_AUTH", True)
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "")
    mcp_client._manager.inject_session("demo", _fake_session([
        {"name": "search", "description": "", "input_schema": {}}]),
        [{"name": "search", "description": "", "input_schema": {}}])
    out = guardrails.guard_tool_call("mcp__demo__search", {"q": "x"}, caller=None)
    assert out["allowed"] is False
    assert "not_allowed" in out["reason"]


def test_guardrails_mcp_allowed_via_wildcard(monkeypatch):
    monkeypatch.setattr(settings, "GUARDRAILS_TOOL_AUTH", True)
    monkeypatch.setattr(settings, "MCP_TOOL_ALLOW", "mcp__demo__*")
    mcp_client._manager.inject_session("demo", _fake_session([
        {"name": "search", "description": "", "input_schema": {}}]),
        [{"name": "search", "description": "", "input_schema": {}}])
    out = guardrails.guard_tool_call("mcp__demo__search", {"q": "x"}, caller=None)
    assert out["allowed"] is True
    assert "result:search" in out["result"]["content"]


def test_guardrails_mcp_tool_auth_off(monkeypatch):
    monkeypatch.setattr(settings, "GUARDRAILS_TOOL_AUTH", False)
    out = guardrails.guard_tool_call("mcp__demo__search", {}, caller=None)
    assert out["allowed"] is False
    assert "GUARDRAILS_TOOL_AUTH=0" in out["reason"]


def test_end_to_end_stdio_connect():
    """启动平台自带的 mcp_stdio_server.py 作为「外部 Server」，经 stdio 真实连接枚举工具。"""
    script = os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "mcp_stdio_server.py")
    script = os.path.abspath(script)
    if not os.path.exists(script):
        pytest.skip("mcp_stdio_server.py 不存在")
    cfg = {
        "name": "self_server",
        "transport": "stdio",
        "command": sys.executable,
        "args": [script],
    }
    m = mcp_client.ExternalMCPManager()
    m.load_from_env(json.dumps([cfg]))
    try:
        asyncio.run(asyncio.wait_for(m.connect_all(), timeout=30))
    except Exception as exc:  # pragma: no cover - 环境差异
        pytest.skip(f"无法真实启动 stdio 子进程：{exc}")
    tools = m.list_tools()
    quals = {t["qualified"] for t in tools}
    # 自带的 Server 暴露 4 个工具
    assert "mcp__self_server__list_agents" in quals
    assert "mcp__self_server__invoke_agent" in quals
    assert "mcp__self_server__search_knowledge" in quals
    assert "mcp__self_server__list_documents" in quals
