"""mcp_bridge 单元测试：验证「平台 REST → 归一化字典」的解析与错误透传。

不依赖真实后端、也不依赖 mcp SDK。通过 monkeypatch _call 注入桩响应，
以及用桩 httpx.Client 验证 _call 本身的连接/状态码/非 JSON 处理。
"""
import pytest

from app.services import mcp_bridge as bridge
from app.services.mcp_bridge import BridgeError


CATALOG = {
    "code": 0,
    "data": {
        "categories": [
            {"category_cn": "制造业", "groups": {"": [
                {"agent_id": "ag_m1", "name": "工艺助手", "status": "published"},
            ]}},
        ],
        "roles": [
            {"category_cn": "财务", "groups": {"": [
                {"agent_id": "ag_f1", "name": "报表助手", "status": "published"},
            ]}},
        ],
        "public": [
            {"agent_id": "ag_pub", "name": "通用助手", "description": "公共库", "status": "published"},
        ],
    },
}

INVOKE = {
    "code": 0,
    "data": {
        "answer": "这是答案",
        "citations": [{"title": "来源A"}],
        "artifacts": [{"filename": "结果.xlsx", "url": "http://x/f", "file_id": "fid1"}],
        "run_id": "run_1",
        "session": {"session_id": "sess_1"},
        "llm_real": True,
    },
}

RETRIEVE = {
    "code": 0,
    "data": {
        "query": "Q",
        "hits": [{"doc_id": "d1", "title": "文档1", "snippet": "摘要…", "score": 0.91, "scope": "dept"}],
        "filtered_out": 2,
    },
}

DOCS = {
    "code": 0,
    "data": {"documents": [{"doc_id": "d1", "title": "文档1", "kb": "kb1"}], "count": 1},
}


def _stub(monkeypatch, path_to_stub: str, payload: dict):
    """把 _call 替换成桩：指定 path 返回 payload，其余走真实 _call（不应被命中）。"""
    orig = bridge._call

    def route(method, base_url, token, path, **kw):
        if path == path_to_stub:
            return payload
        return orig(method, base_url, token, path, **kw)

    monkeypatch.setattr(bridge, "_call", route)


def test_list_agents_parses_catalog(monkeypatch):
    _stub(monkeypatch, "/agents/catalog", CATALOG)
    agents = bridge.list_agents("http://x", "tok")
    ids = {a["id"] for a in agents}
    assert {"ag_m1", "ag_f1", "ag_pub"} <= ids
    assert all(a["name"] for a in agents)
    assert [a["id"] for a in agents].count("ag_pub") == 1  # 去重


def test_invoke_agent_returns_normalized(monkeypatch):
    _stub(monkeypatch, "/invoke", INVOKE)
    r = bridge.invoke_agent("http://x", "tok", "ag_m1", "你好")
    assert r["answer"] == "这是答案"
    assert r["citations"][0]["title"] == "来源A"
    assert r["artifacts"][0]["filename"] == "结果.xlsx"
    assert r["run_id"] == "run_1"
    assert r["session_id"] == "sess_1"
    assert r["llm_real"] is True


def test_search_knowledge_normalizes_hits(monkeypatch):
    _stub(monkeypatch, "/rag/retrieve", RETRIEVE)
    r = bridge.search_knowledge("http://x", "tok", "Q", top_k=6)
    assert r["hits"][0]["doc_id"] == "d1"
    assert r["hits"][0]["score"] == 0.91
    assert r["filtered_out"] == 2


def test_list_documents_normalizes(monkeypatch):
    _stub(monkeypatch, "/kb/documents", DOCS)
    docs = bridge.list_documents("http://x", "tok")
    assert docs[0]["doc_id"] == "d1"
    assert docs[0]["title"] == "文档1"


def test_call_raises_on_non_2xx(monkeypatch):
    class FakeResp:
        status_code = 401
        text = "unauthorized"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            return FakeResp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bridge.httpx, "Client", FakeClient)
    with pytest.raises(BridgeError):
        bridge._call("GET", "http://x", "tok", "/agents/catalog")


def test_call_raises_on_connection_error(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise bridge.httpx.ConnectError("refused")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bridge.httpx, "Client", FakeClient)
    with pytest.raises(BridgeError):
        bridge._call("GET", "http://x", "tok", "/agents/catalog")


def test_call_raises_on_non_json(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "<html>not json</html>"

        def json(self):
            raise ValueError("no json")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            return FakeResp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bridge.httpx, "Client", FakeClient)
    with pytest.raises(BridgeError):
        bridge._call("GET", "http://x", "tok", "/agents/catalog")
