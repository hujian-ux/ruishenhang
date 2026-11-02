"""M6 基础设施接入测试：真实 LLM（探活降级）/ PG 写穿（离线兜底）/ MinIO（离线兜底）+ kb 读写闭环。

沙箱/CI 无 docker-compose 时全部走降级路径；用户机器 compose up 后同代码自动启用真设施。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import model_gateway, object_store, pg_store
from app.services import approval_store, session_store

client = TestClient(app)
AUTH = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}
OTHER = {"Authorization": create_dev_token(user_id="u_2002", dept_id="d_sales", role="employee")}
ADMIN = {"Authorization": create_dev_token(user_id="u_9000", dept_id="d_it", role="platform_admin")}


# ── ① 模型网关：探活降级 + stub 可用 ──

def test_llm_probe_offline_is_false_and_stub_works():
    # 沙箱无 LiteLLM → 探活 False → stub 兜底（不抛异常）
    if model_gateway.llm_available():
        return  # 本机 compose up 时探活为真，跳过离线断言
    answer = model_gateway.chat_sync("gpt-4o-mini", [
        {"role": "user", "content": "你好"}])
    assert answer.startswith("[stub:")
    assert "你好" in answer


def test_invoke_uses_gateway_with_fallback():
    # 注册→审批→发布→调用：无论 LLM 是否在线，回答都非空且带轮次标记
    r = client.post("/api/v1/agents",
                    json={"name": "M6网关助手", "adapter_type": "llm_retrieval",
                          "description": "x", "knowledge_scope": "department"}, headers=AUTH)
    d = r.json()["data"]
    client.post(f"/api/v1/approval/{d['approval_id']}/decide",
                json={"decision": "approve"}, headers=ADMIN)
    inv = client.post("/api/v1/agent/invoke",
                      json={"agent_id": d["agent_id"], "input": "五一活动方案"},
                      headers=AUTH)
    assert inv.status_code == 200
    answer = inv.json()["data"]["answer"]
    assert answer  # LLM 在线→真生成；离线→stub；都不为空


# ── ② PG 写穿：离线不崩、内存兜底 ──

def test_pg_offline_fallback_keeps_memory_working():
    if pg_store.available():
        return  # PG 在线时写穿真实生效，离线断言跳过
    assert pg_store.available() is False
    assert pg_store.upsert("agent_sessions", "sess_x", {"a": 1}) is False
    # 业务层不受影响：会话/审批照常
    sess = session_store.create_session("ag_x", type("C", (), {
        "userId": "u_1024", "deptId": "d_sales", "role": "employee", "tenantId": "t_demo"})())
    session_store.append_turn(sess["session_id"], "user", "hello")
    assert len(session_store.history_of(sess["session_id"])) == 1
    t = approval_store.create_ticket("ag_x", "u_1024", "d_sales")
    assert t["status"] == "pending"


# ── ③ MinIO / kb 读写闭环 ──

def test_kb_create_list_and_content_roundtrip():
    r = client.post("/api/v1/kb/documents",
                    json={"title": "M6对象存储测试", "content": "五一活动预算明细：到店礼5000元。",
                          "scope": "department"}, headers=AUTH)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["doc_id"].startswith("doc_")
    assert d["storage"].startswith(("minio://", "memory://"))  # 在线/离线二选一

    # 创建者可见
    lst = client.get("/api/v1/kb/documents", headers=AUTH).json()["data"]
    assert any(x["doc_id"] == d["doc_id"] for x in lst["documents"])

    # 原文读回（对象存储写读闭环）
    c = client.get(f"/api/v1/kb/documents/{d['doc_id']}/content", headers=AUTH)
    assert c.status_code == 200
    assert "到店礼5000元" in c.json()["data"]["content"]


def test_kb_visibility_across_users():
    r = client.post("/api/v1/kb/documents",
                    json={"title": "私人笔记", "content": "secret-content", "scope": "personal"},
                    headers=AUTH)
    doc_id = r.json()["data"]["doc_id"]
    # 本人可见
    mine = client.get("/api/v1/kb/documents", headers=AUTH).json()["data"]
    assert any(x["doc_id"] == doc_id for x in mine["documents"])
    # 他人 personal 不可见
    theirs = client.get("/api/v1/kb/documents", headers=OTHER).json()["data"]
    assert not any(x["doc_id"] == doc_id for x in theirs["documents"])
    # 他人直接取原文 → 403
    assert client.get(f"/api/v1/kb/documents/{doc_id}/content", headers=OTHER).status_code == 403


def test_kb_unknown_doc_404():
    r = client.get("/api/v1/kb/documents/doc_nosuch/content", headers=AUTH)
    assert r.status_code == 404
