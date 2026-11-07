"""M7 测试：计量真实化 / 知识库上传 / 会话管理 / 模型管理。

覆盖：
  ① 每次调用落一单账（token/成本/真伪/延迟），/usage/records 可查
  ② 多维聚合 dept/agent/model/user/day，且 RBAC 收敛（员工→本人、部门管理员→本部门、平台→全量）
  ③ 配额红线：部门 token 超限 → 调用直接 429（不是事后告警）
  ④ 知识库文件上传：落对象存储 + 入检索语料（上传即可被 RAG 召回）+ 删除后不可召回
  ⑤ 会话列表/删除（归属校验），模型启停（平台管理员专属）
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.services import model_router, usage_store

client = TestClient(app)

SALES = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}
DESIGN = {"Authorization": create_dev_token(user_id="u_3000", dept_id="d_design", role="employee")}
DEPT_ADMIN = {"Authorization": create_dev_token(user_id="u_8001", dept_id="d_sales", role="dept_admin")}
ADMIN = {"Authorization": create_dev_token(user_id="u_9000", dept_id="d_it", role="platform_admin")}


def _published_agent(name: str = "计量测试助手") -> str:
    """注册 + 部门管理员审批通过 → 可调用的智能体（M5 发布红线前置）。"""
    r = client.post("/api/v1/agents",
                    json={"name": name, "adapter_type": "llm_retrieval",
                          "description": "M7", "knowledge_scope": "department"},
                    headers=SALES)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    r2 = client.post(f"/api/v1/approval/{d['approval_id']}/decide",
                     json={"decision": "approve"}, headers=DEPT_ADMIN)
    assert r2.status_code == 200, r2.text
    return d["agent_id"]


def _invoke(agent_id: str, text: str = "知识库混合检索的原理是什么") -> dict:
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": agent_id, "input": text}, headers=SALES)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# P2-40：路由已 applied，计量按实际（路由后）模型落账，而非固定默认模型。
def _routed_model_for(text: str) -> str:
    if not settings.ROUTING_ENABLED:
        return settings.LLM_DEFAULT_MODEL
    return model_router.route(text, {})["model"]


# ─────────────────────────────────────────── ① 落账 + ② 聚合 + ③ RBAC
def test_invoke_records_usage():
    agent_id = _published_agent("落账验证助手")
    data = _invoke(agent_id)
    u = data["usage"]
    assert u["total_tokens"] > 0, u
    assert u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]
    assert u["model_id"] == _routed_model_for("知识库混合检索的原理是什么")
    assert u["currency"] == "CNY"
    assert isinstance(u["real"], bool)
    # 配额状态同响应回显（页面顶部水位条用）
    assert data["quota"]["quota_tokens"] == settings.USAGE_QUOTA_TOKENS
    assert 0 <= data["quota"]["used_pct"] <= 100

    recs = client.get("/api/v1/usage/records?limit=50", headers=SALES).json()["data"]["records"]
    row = next(r for r in recs if r["agent_id"] == agent_id)
    assert row["prompt_tokens"] > 0 and row["completion_tokens"] > 0
    assert row["cost"] >= 0 and row["user_id"] == "u_1024" and row["dept_id"] == "d_sales"


def test_summary_dimensions():
    agent_id = _published_agent("维度聚合助手")
    _invoke(agent_id)
    _invoke(agent_id, "再问一次：预算怎么算")

    for dim in ("dept", "agent", "model", "user", "day"):
        r = client.get(f"/api/v1/usage/summary?dimension={dim}", headers=ADMIN)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["dimension"] == dim and data["rows"], f"{dim} 维度应有聚合行"
        assert data["total"]["calls"] >= 2
        assert data["total"]["total_tokens"] > 0

    agents_dim = client.get("/api/v1/usage/summary?dimension=agent", headers=ADMIN).json()["data"]
    row = next(x for x in agents_dim["rows"] if x["key"] == agent_id)
    assert row["calls"] >= 2 and row["total_tokens"] == row["prompt_tokens"] + row["completion_tokens"]

    bad = client.get("/api/v1/usage/summary?dimension=nope", headers=ADMIN)
    assert bad.status_code == 422


def test_usage_rbac_scope():
    agent_id = _published_agent("RBAC计量助手")
    _invoke(agent_id)

    # 员工：只看到自己的账
    mine = client.get("/api/v1/usage/records?limit=200", headers=SALES).json()["data"]["records"]
    assert mine and all(r["user_id"] == "u_1024" for r in mine)

    # 其他部门员工：看不到销售部的账
    theirs = client.get("/api/v1/usage/records?limit=200", headers=DESIGN).json()["data"]["records"]
    assert all(r["user_id"] == "u_3000" for r in theirs)
    assert all(r["agent_id"] != agent_id for r in theirs)

    # 部门管理员：只看本部门
    dept = client.get("/api/v1/usage/summary?dimension=dept", headers=DEPT_ADMIN).json()["data"]
    assert [r["key"] for r in dept["rows"]] == ["d_sales"]

    # 平台管理员：全量（至少包含销售部且行数不少于部门管理员）
    all_rows = client.get("/api/v1/usage/summary?dimension=dept", headers=ADMIN).json()["data"]
    keys = {r["key"] for r in all_rows["rows"]}
    assert "d_sales" in keys and all_rows["total"]["calls"] >= dept["total"]["calls"]


def test_quota_red_line_blocks_invoke():
    agent_id = _published_agent("配额红线助手")
    old = settings.USAGE_QUOTA_TOKENS
    try:
        settings.USAGE_QUOTA_TOKENS = 1  # 让本部门必然超限
        r = client.post("/api/v1/agent/invoke",
                        json={"agent_id": agent_id, "input": "还能问吗"}, headers=SALES)
        assert r.status_code == 429, r.text
        assert "quota exceeded" in r.json()["detail"]
        st = client.get("/api/v1/usage/quota", headers=SALES).json()["data"]
        assert st["exceeded"] is True
    finally:
        settings.USAGE_QUOTA_TOKENS = old
    # 恢复配额后可继续调用（红线是拦截不是封号）
    assert _invoke(agent_id, "恢复后再问一次")["answer"]


# ─────────────────────────────────────────── ④ 知识库上传 → 可召回 → 删除
UNIQUE = "鲸落验收清单"


def test_kb_upload_then_recall_then_delete():
    body = f"{UNIQUE}：包含三项验收动作，逐项签字确认，缺失任一项不得上线。"
    r = client.post("/api/v1/kb/documents/upload",
                    files={"file": ("验收清单.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
                    data={"title": UNIQUE, "scope": "department"},
                    headers=SALES)
    assert r.status_code == 200, r.text
    doc = r.json()["data"]
    assert doc["indexed"] is True and doc["chars"] == len(body)
    assert doc["source"] == "upload" and doc["dept_id"] == "d_sales"
    doc_id = doc["doc_id"]

    # 原文可从对象存储读回
    got = client.get(f"/api/v1/kb/documents/{doc_id}/content", headers=SALES).json()["data"]
    assert got["content"] == body

    # 上传后即可被 RAG 召回（同一份索引，不是两份数据）
    hits = client.post("/api/v1/rag/retrieve",
                       json={"query": UNIQUE, "kb_scope": "all", "top_k": 6, "tenant_id": "t_demo"},
                       headers=SALES).json()["data"]["hits"]
    assert doc_id in {h["doc_id"] for h in hits}, [h["doc_id"] for h in hits]

    # 删除后三处同步清理：检索不到了
    assert client.delete(f"/api/v1/kb/documents/{doc_id}", headers=SALES).status_code == 200
    hits2 = client.post("/api/v1/rag/retrieve",
                        json={"query": UNIQUE, "kb_scope": "all", "top_k": 6, "tenant_id": "t_demo"},
                        headers=SALES).json()["data"]["hits"]
    assert doc_id not in {h["doc_id"] for h in hits2}
    assert client.get(f"/api/v1/kb/documents/{doc_id}/content", headers=SALES).status_code == 404


def test_kb_upload_guards():
    exe = client.post("/api/v1/kb/documents/upload",
                      files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
                      headers=SALES)
    assert exe.status_code == 415
    glob = client.post("/api/v1/kb/documents/upload",
                       files={"file": ("a.txt", io.BytesIO("x".encode()), "text/plain")},
                       data={"scope": "global"}, headers=SALES)
    assert glob.status_code == 403  # 员工不可写 global
    cross = client.post("/api/v1/kb/documents/upload",
                        files={"file": ("a.txt", io.BytesIO("x".encode()), "text/plain")},
                        data={"scope": "department", "dept_id": "d_design"}, headers=SALES)
    assert cross.status_code == 403  # 跨部门写入被拦


# ─────────────────────────────────────────── ⑤ 会话管理 + 模型启停
def test_sessions_list_and_delete():
    agent_id = _published_agent("会话管理助手")
    data = _invoke(agent_id, "会话管理测试")
    sid = data["session"]["session_id"]

    mine = client.get("/api/v1/agent/sessions", headers=SALES).json()["data"]["sessions"]
    assert sid in {s["session_id"] for s in mine}
    assert next(s for s in mine if s["session_id"] == sid)["turn_count"] == 2

    # 归属校验：他人不可读/不可删
    assert client.get(f"/api/v1/agent/sessions/{sid}", headers=DESIGN).status_code == 403
    assert client.delete(f"/api/v1/agent/sessions/{sid}", headers=DESIGN).status_code == 403
    # 全量列表仅平台管理员
    assert client.get("/api/v1/agent/sessions?all=true", headers=SALES).status_code == 403
    assert client.get("/api/v1/agent/sessions?all=true", headers=ADMIN).status_code == 200

    assert client.delete(f"/api/v1/agent/sessions/{sid}", headers=SALES).status_code == 200
    assert client.get(f"/api/v1/agent/sessions/{sid}", headers=SALES).status_code == 404


def test_model_toggle_and_gateway_health():
    gh = client.get("/api/v1/models/gateway", headers=SALES).json()["data"]
    assert isinstance(gh["available"], bool) and gh["default_model"]

    assert client.patch("/api/v1/models/qwen-turbo", json={"status": "inactive"},
                        headers=SALES).status_code == 403
    assert client.patch("/api/v1/models/qwen-turbo", json={"status": "bogus"},
                        headers=ADMIN).status_code == 422
    assert client.patch("/api/v1/models/nope", json={"status": "inactive"},
                        headers=ADMIN).status_code == 404

    try:
        r = client.patch("/api/v1/models/qwen-turbo", json={"status": "inactive"}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["data"]["model"]["status"] == "inactive"
        active = client.get("/api/v1/models", headers=SALES).json()["data"]["models"]
        assert "qwen-turbo" not in {m["model_id"] for m in active}
        every = client.get("/api/v1/models?status=all", headers=SALES).json()["data"]["models"]
        assert "qwen-turbo" in {m["model_id"] for m in every}
    finally:
        client.patch("/api/v1/models/qwen-turbo", json={"status": "active"}, headers=ADMIN)


def test_usage_reset_helper():
    """usage_store.reset() 供本地重置/测试隔离（不动 PG）。"""
    usage_store.reset()
    assert usage_store.summary("dept", caller=_caller()).get("rows") == []


def _caller():
    from app.models.caller import CallerContext
    return CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
