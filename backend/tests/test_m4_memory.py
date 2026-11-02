"""M4 智能体编排与会话记忆测试：invoke 端点、多轮记忆、归属校验、窗口封顶。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import session_store

client = TestClient(app)
# 张三：销售部员工（会话创建者）
AUTH = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}
# 李四：另一位销售部员工（越权访问张三会话用）
OTHER_AUTH = {"Authorization": create_dev_token(user_id="u_2002", dept_id="d_sales", role="employee")}


def _register_agent() -> str:
    r = client.post("/api/v1/agents",
                    json={"name": "营销知识助手", "adapter_type": "llm_retrieval",
                          "description": "营销知识库问答", "knowledge_scope": "department"},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["data"]["agent_id"]


def test_invoke_creates_session():
    agent_id = _register_agent()
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": agent_id, "input": "查一下五一活动方案"},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["answer"]
    assert data["session"]["session_id"].startswith("sess_")
    assert data["session"]["turn_count"] == 2  # user + assistant
    assert data["session"]["query_rewritten"] is False  # 首轮无历史
    assert data["session"]["turn_no"] == 1


def test_invoke_second_turn_uses_history():
    agent_id = _register_agent()
    # 第一轮
    r1 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": agent_id, "input": "五一活动方案有哪些"},
                     headers=AUTH)
    sid = r1.json()["data"]["session"]["session_id"]
    # 第二轮：带指代（"预算多少"），应改写 query 并引用历史
    r2 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": agent_id, "input": "预算多少", "session_id": sid},
                     headers=AUTH)
    assert r2.status_code == 200, r2.text
    data = r2.json()["data"]
    assert data["session"]["session_id"] == sid
    assert data["session"]["turn_count"] == 4  # 前一轮 2 + 本轮 2
    assert data["session"]["turn_no"] == 2
    assert data["session"]["query_rewritten"] is True  # 历史并入检索 query
    # 记忆注入生成上下文：答案应含首轮问题关键词（确定性 stub 中可断言）
    assert "五一" in data["answer"]


def test_session_memory_persists_and_query_endpoint():
    agent_id = _register_agent()
    r1 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": agent_id, "input": "品牌规范在哪"},
                     headers=AUTH)
    sid = r1.json()["data"]["session"]["session_id"]
    # 创建者本人可查会话记忆
    r = client.get(f"/api/v1/agent/sessions/{sid}", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["turn_count"] == 2
    assert body["agent_id"] == agent_id
    assert "品牌规范在哪" in body["context_preview"]


def test_session_access_control():
    agent_id = _register_agent()
    r1 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": agent_id, "input": "我的笔记"},
                     headers=AUTH)
    sid = r1.json()["data"]["session"]["session_id"]
    # 他人查看张三会话 → 403
    r_view = client.get(f"/api/v1/agent/sessions/{sid}", headers=OTHER_AUTH)
    assert r_view.status_code == 403
    # 他人用该 session_id 续聊 → 403
    r_invoke = client.post("/api/v1/agent/invoke",
                           json={"agent_id": agent_id, "input": "接着聊", "session_id": sid},
                           headers=OTHER_AUTH)
    assert r_invoke.status_code == 403


def test_invoke_unknown_agent_and_session():
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": "ag_nosuch", "input": "hi"},
                    headers=AUTH)
    assert r.status_code == 404
    # 伪造不存在的 session_id → 404
    agent_id = _register_agent()
    r2 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": agent_id, "input": "hi", "session_id": "sess_nosuch"},
                     headers=AUTH)
    assert r2.status_code == 404


def test_memory_window_cap():
    """上下文窗口封顶：超出 MAX_CONTEXT_TURNS 只取最近 N 轮（滑动窗口）。"""
    sess = {"turns": [{"role": "user", "content": f"问题{i}"} for i in range(20)]}
    ctx = session_store.build_context(sess)
    assert "问题19" in ctx  # 最近的在
    assert "问题0" not in ctx  # 最早的被截断
    # 轮数上限：MAX_SESSION_TURNS 滑动丢弃
    sess2 = session_store.create_session("ag_x", type("C", (), {
        "userId": "u_win", "deptId": "d_sales", "role": "employee", "tenantId": "t_demo"})())
    for i in range(50):
        session_store.append_turn(sess2["session_id"], "user", f"t{i}")
    turns = session_store.history_of(sess2["session_id"])
    assert len(turns) == session_store.MAX_SESSION_TURNS
    assert turns[-1]["content"] == "t49"
    assert turns[0]["content"] != "t0"  # 最早的已丢弃


def test_cross_agent_session_isolated():
    """会话绑定智能体：换一个 agent 传同 session_id → 404（防跨智能体串记忆）。"""
    a1 = _register_agent()
    a2 = _register_agent()
    r1 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": a1, "input": "第一轮"},
                     headers=AUTH)
    sid = r1.json()["data"]["session"]["session_id"]
    r2 = client.post("/api/v1/agent/invoke",
                     json={"agent_id": a2, "input": "续聊", "session_id": sid},
                     headers=AUTH)
    assert r2.status_code == 404
