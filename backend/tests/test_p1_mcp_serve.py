"""P1-2 MCP 服务端暴露回归测试。

覆盖：
- initialize / tools/list 返回能力声明与 4 个工具（kb_search/skill_invoke/artifact_get/list_skills）。
- tools/call：kb_search / skill_invoke / artifact_get / list_skills 各自返回 content 文本
  （内部服务层全部 monkeypatch，不依赖真实检索/模型/文件）。
- 鉴权：缺 Authorization → 401（复用网关 decode_token，租户隔离随 caller）。
- 配额：MCP_QUOTA_PER_MIN 限制，超限返回 -32002。
- 开关：MCP_SERVE_ENABLED=False → 503。
- 发现：GET /api/v1/mcp/serve 与 /.well-known/mcp 返回连接信息（端点/传输/工具）。
- 通知：notifications/* 返回 202 无响应体。
"""
from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.services import agent_runtime, agent_store, file_store, knowledge
from app.services.mcp_server import reset_quota
from fastapi.testclient import TestClient

API = "/api/v1"
TOKEN = create_dev_token("u1", "d1", "employee", "t_demo")
H = {"Authorization": TOKEN}


def _rpc(method: str, params: dict = None, msg_id=1) -> dict:
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return TestClient(app).post(f"{API}/mcp/serve", json=body, headers=H).json()


def test_mcp_initialize():
    r = TestClient(app).post(f"{API}/mcp/serve",
                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers=H)
    assert r.status_code == 200
    data = r.json()["result"]
    assert data["protocolVersion"] == "2024-11-05"
    assert "tools" in data["capabilities"]


def test_mcp_tools_list():
    r = _rpc("tools/list")
    assert r["result"]["tools"]
    names = {t["name"] for t in r["result"]["tools"]}
    assert {"kb_search", "skill_invoke", "artifact_get", "list_skills"} <= names


def test_mcp_kb_search(monkeypatch):
    monkeypatch.setattr(knowledge, "retrieve",
                       lambda q, caller, top_k=6: (
                           [{"title": "手册", "snippet": "要点A", "score": 0.9, "scope": "dept"}],
                           0))
    r = _rpc("tools/call", {"name": "kb_search", "arguments": {"query": "怎么报销"}})
    text = r["result"]["content"][0]["text"]
    assert "手册" in text and "命中 1 条" in text


def test_mcp_skill_invoke(monkeypatch):
    monkeypatch.setattr(agent_runtime, "run_agent",
                       lambda agent_id, user_input, caller, session_id="": {
                           "answer": "已生成方案", "citations": [{"title": "依据"}],
                           "artifacts": [{"file_id": "f_1", "filename": "方案.docx"}],
                           "llm_real": False})
    r = _rpc("tools/call", {"name": "skill_invoke",
                            "arguments": {"agent_id": "a1", "query": "写方案"}})
    text = r["result"]["content"][0]["text"]
    assert "已生成方案" in text and "f_1" in text


def test_mcp_artifact_get(monkeypatch):
    rec = {"filename": "方案.md", "owner_id": "u1"}
    monkeypatch.setattr(file_store, "get", lambda fid: rec)
    monkeypatch.setattr(file_store, "can_access", lambda rec, caller: True)
    monkeypatch.setattr(file_store, "read", lambda rec: "方案正文内容".encode("utf-8"))
    r = _rpc("tools/call", {"name": "artifact_get", "arguments": {"file_id": "f_1"}})
    text = r["result"]["content"][0]["text"]
    assert "方案.md" in text and "方案正文内容" in text


def test_mcp_artifact_get_forbidden(monkeypatch):
    rec = {"filename": "secret.docx", "owner_id": "other"}
    monkeypatch.setattr(file_store, "get", lambda fid: rec)
    monkeypatch.setattr(file_store, "can_access", lambda rec, caller: False)
    r = _rpc("tools/call", {"name": "artifact_get", "arguments": {"file_id": "f_x"}})
    assert r["error"]["code"] == -32001


def test_mcp_list_skills(monkeypatch):
    monkeypatch.setattr(agent_store, "catalog",
                       lambda caller, role="": {
                           "categories": [], "roles": [],
                           "public": [{"agent_id": "a1", "name": "报销助手", "description": "d"}]})
    r = _rpc("tools/call", {"name": "list_skills", "arguments": {}})
    text = r["result"]["content"][0]["text"]
    assert "a1" in text and "报销助手" in text


def test_mcp_missing_auth():
    r = TestClient(app).post(f"{API}/mcp/serve",
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_mcp_quota_exceeded(monkeypatch):
    orig = settings.MCP_QUOTA_PER_MIN
    monkeypatch.setattr(settings, "MCP_QUOTA_PER_MIN", 2)
    reset_quota()
    monkeypatch.setattr(knowledge, "retrieve",
                       lambda q, caller, top_k=6: ([], 0))
    try:
        assert _rpc("tools/call", {"name": "kb_search", "arguments": {"query": "q"}})["result"]
        assert _rpc("tools/call", {"name": "kb_search", "arguments": {"query": "q"}})["result"]
        third = _rpc("tools/call", {"name": "kb_search", "arguments": {"query": "q"}})
        assert third["error"]["code"] == -32002
    finally:
        settings.MCP_QUOTA_PER_MIN = orig
        reset_quota()


def test_mcp_serve_disabled(monkeypatch):
    orig = settings.MCP_SERVE_ENABLED
    monkeypatch.setattr(settings, "MCP_SERVE_ENABLED", False)
    try:
        r = TestClient(app).post(f"{API}/mcp/serve",
                                 json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers=H)
        assert r.status_code == 503
    finally:
        settings.MCP_SERVE_ENABLED = orig


def test_mcp_discovery_get():
    r = TestClient(app).get(f"{API}/mcp/serve")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["mcp_endpoint"].endswith("/mcp/serve")
    assert len(d["tools"]) == 4


def test_mcp_well_known():
    r = TestClient(app).get("/.well-known/mcp")
    assert r.status_code == 200
    assert r.json()["mcp_endpoint"].endswith("/mcp/serve")


def test_mcp_notification_no_content():
    r = TestClient(app).post(f"{API}/mcp/serve",
                             json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=H)
    assert r.status_code == 202
