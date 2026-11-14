"""P0-4 跨会话长期记忆：存储 / 查询 / 抽取 / 注入 的单元与集成测试。

无模型环境下 memory_store 不依赖 PG（内存为主存），llm_available()=False 时抽取零开销跳过。
"""
import pytest
from fastapi.testclient import TestClient

import app.services.agent_runtime as agent_runtime
import app.services.memory_store as ms
import app.services.model_gateway as mg
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext

client = TestClient(app)
# P0 收尾3：记忆端点用与 CALLER_A 完全一致的 dev token（同 userId/tenant 才能命中）
MEM_TOKEN = {"Authorization": create_dev_token("u_a", "d1", "user", "t1")}

CALLER_A = CallerContext(userId="u_a", deptId="d1", role="user", tenantId="t1")
CALLER_A2 = CallerContext(userId="u_a", deptId="d1", role="user", tenantId="t1")  # 同人、不同会话
CALLER_TEAMMATE = CallerContext(userId="u_x", deptId="d1", role="user", tenantId="t1")  # 同部门不同人
CALLER_B = CallerContext(userId="u_b", deptId="d2", role="user", tenantId="t2")  # 不同租户


@pytest.fixture(autouse=True)
def _clear_facts():
    ms._FACTS.clear()
    yield
    ms._FACTS.clear()


def test_remember_visible_across_sessions():
    """同 caller 不同会话写入的事实，跨会话都能被 relevant_facts 取到。"""
    ms.remember(CALLER_A, "用户在东莞做储能项目", category="fact", session_id="s1")
    # 用另一个 session_id 的同一 caller 查询
    block = ms.relevant_facts(CALLER_A2, agent_id="a1")
    assert "东莞" in block
    assert "长期记忆" in block


def test_relevant_facts_format_and_label():
    ms.remember(CALLER_A, "偏好用中文回复", category="preference")
    block = ms.relevant_facts(CALLER_A)
    assert "- (偏好) 偏好用中文回复" in block


def test_tenant_isolation():
    """跨租户不得串记忆（tenant 隔离红线）。"""
    ms.remember(CALLER_A, "东莞储能项目", category="fact")
    assert "东莞" not in ms.relevant_facts(CALLER_B)
    assert ms.list_facts(CALLER_B) == []


def test_dept_sharing_but_not_cross_tenant():
    """同租户同部门不同人可共享（scope=dept）；但跨租户仍不可见。"""
    ms.remember(CALLER_A, "部门规范：报价用元", category="constraint", scope="dept")
    # 同部门同事可见
    assert "报价用元" in ms.relevant_facts(CALLER_TEAMMATE)
    # 跨租户不可见
    assert "报价用元" not in ms.relevant_facts(CALLER_B)


def test_agent_scoped_fact_not_leak():
    """仅某智能体可见的事实，跨 agent 不命中。"""
    ms.remember(CALLER_A, "专用术语解释", category="fact", agent_id="agent_special")
    assert "专用术语解释" not in ms.relevant_facts(CALLER_A, agent_id="agent_other")
    assert "专用术语解释" in ms.relevant_facts(CALLER_A, agent_id="agent_special")


def test_dedup_same_content():
    """同租户同内容去重，不重复建。"""
    id1 = ms.remember(CALLER_A, "用户公司在东莞")
    id2 = ms.remember(CALLER_A, "用户公司在东莞")
    assert id1 == id2
    assert len(ms.list_facts(CALLER_A)) == 1


def test_forget_soft_delete():
    fid = ms.remember(CALLER_A, "临时偏好X", category="preference")
    assert ms.forget(fid, CALLER_A) is True
    assert ms.list_facts(CALLER_A) == []


def test_forget_cross_tenant_blocked():
    fid = ms.remember(CALLER_A, "租户1的事实")
    assert ms.forget(fid, CALLER_B) is False  # 跨租户不可删
    assert len(ms.list_facts(CALLER_A)) == 1


def test_extract_into_parses_memory_fence(monkeypatch):
    """模型返回 ```memory 块时正确解析并入库。"""
    monkeypatch.setattr(mg, "llm_available", lambda: True)

    def fake_chat(model_id, messages, **kw):
        return {"text": (
            "已记下。\n```memory\n"
            '[{"category":"preference","content":"用户偏好用中文回复"},'
            '{"category":"fact","content":"用户公司在东莞做储能"}]\n```'
        )}

    monkeypatch.setattr(mg, "chat_sync_ex", fake_chat)
    ids = ms.extract_into(CALLER_A, "以后都用中文回我，我在东莞做储能", "好的，记下了",
                          agent_id="a1", session_id="s1")
    assert len(ids) == 2
    contents = [f["content"] for f in ms.list_facts(CALLER_A)]
    assert "用户偏好用中文回复" in contents
    assert "用户公司在东莞做储能" in contents


def test_extract_into_skips_when_no_llm(monkeypatch):
    """无模型（llm_available=False）时零开销跳过，绝不调 chat_sync_ex。"""
    called = {"n": 0}
    monkeypatch.setattr(mg, "llm_available", lambda: False)

    def fake_chat(*a, **k):
        called["n"] += 1
        return {"text": "x"}

    monkeypatch.setattr(mg, "chat_sync_ex", fake_chat)
    ids = ms.extract_into(CALLER_A, "问题", "答案")
    assert ids == []
    assert called["n"] == 0  # 未发起任何模型调用


def test_extract_into_handles_bad_json(monkeypatch):
    """模型返回非法 JSON 时安全空返，不报错。"""
    monkeypatch.setattr(mg, "llm_available", lambda: True)
    monkeypatch.setattr(mg, "chat_sync_ex",
                        lambda *a, **k: {"text": "```memory\n不是合法JSON\n```"})
    assert ms.extract_into(CALLER_A, "x", "y") == []
    assert ms.list_facts(CALLER_A) == []


def test_run_agent_injects_memory(monkeypatch):
    """run_agent 在生成前调用 relevant_facts 把长期记忆注入 system_prompt。"""
    calls = []

    def fake_relevant(caller, agent_id=None, limit=None):
        calls.append((caller, agent_id))
        return ("【长期记忆（来自与你的历史对话，跨会话保留，优先遵循）】\n"
                "- (偏好) 用户偏好用中文回复")

    monkeypatch.setattr(ms, "relevant_facts", fake_relevant)
    # 测试环境无模型：run_agent 走 graph 失败 fallback stub，仍生成答案
    res = agent_runtime.run_agent("agent_x", "普通问题", CALLER_A)
    assert res.get("answer")
    assert calls, "relevant_facts 应被调用以注入长期记忆"
    assert calls[0][0].tenantId == CALLER_A.tenantId


# ───────────────────────── P0 收尾3：记忆管理端点 ─────────────────────────
def test_memory_endpoint_list_and_delete():
    """GET /agent/memory 列出当前 caller 可见事实；DELETE 可删除（软删）。"""
    fid = ms.remember(CALLER_A, "用户在东莞做储能", category="fact", session_id="s_mem")
    r = client.get("/api/v1/agent/memory", headers=MEM_TOKEN)
    assert r.status_code == 200, r.text[:200]
    body = r.json()["data"]
    assert body["count"] >= 1
    hit = next((f for f in body["facts"] if f["fact_id"] == fid), None)
    assert hit is not None
    assert hit["content"] == "用户在东莞做储能"
    assert hit["scope"] == "user"  # CALLER_A 默认 scope=user

    d = client.delete(f"/api/v1/agent/memory/{fid}", headers=MEM_TOKEN)
    assert d.status_code == 200
    assert d.json()["data"]["deleted"] is True

    r2 = client.get("/api/v1/agent/memory", headers=MEM_TOKEN)
    assert all(f["fact_id"] != fid for f in r2.json()["data"]["facts"])


def test_memory_endpoint_delete_forbids_cross_tenant():
    """跨租户不可删：用其它租户的 token 删 CALLER_A 的事实应 404。"""
    fid = ms.remember(CALLER_A, "东莞储能项目X", category="fact")
    other = {"Authorization": create_dev_token("u_b", "d2", "user", "t2")}
    d = client.delete(f"/api/v1/agent/memory/{fid}", headers=other)
    assert d.status_code == 404
    # 原事实仍在（未被误删）
    assert any(f["fact_id"] == fid for f in ms.list_facts(CALLER_A))
