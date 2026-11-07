"""M46b · 审批助手（辅助层）：assist 接口返回建议、GET 详情含 stages、助手智能体注册且不污染 115 口径。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store, approval_store

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_admin_a", "d_it", "platform_admin", "t_demo")}


def _register(name="M46b助手测试"):
    emp = {"Authorization": create_dev_token("u_emp_a", "d_sales", "employee", "t_demo")}
    r = client.post("/api/v1/agents",
                    json={"name": name, "adapter_type": "llm_retrieval",
                          "description": "M46b 辅助测试", "knowledge_scope": "department"}, headers=emp)
    assert r.status_code == 200, r.text
    return r.json()["data"]["approval_id"], emp


def test_assist_returns_draft_summary_compliance():
    apr_id, emp = _register()
    r = client.post(f"/api/v1/approval/{apr_id}/assist", headers=emp)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert "draft" in d and "summary" in d and "compliance_flags" in d
    assert "不落账" in d["note"]
    assert d["summary"]["ticket_id"] == apr_id
    # 申请人本人可见；未授权他人不可见
    other = {"Authorization": create_dev_token("u_other", "d_design", "employee", "t_demo")}
    assert client.post(f"/api/v1/approval/{apr_id}/assist", headers=other).status_code == 403


def test_get_detail_exposes_stages():
    apr_id, emp = _register()
    r = client.get(f"/api/v1/approval/{apr_id}", headers=emp)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total_stages"] >= 1
    assert d["stages"] and isinstance(d["stages"], list)
    assert "current_stage" in d and "finished" in d


def test_approval_assistant_registered_and_outside_115():
    a = agent_store.ensure_approval_assistant()
    assert a["agent_id"] == agent_store.APPROVAL_ASSISTANT_ID
    assert a.get("builtin") is True
    # catalog 不把内置/审批助手计入业务口径
    before = client.get("/api/v1/agents/catalog", headers=_admin()).json()["data"]["total"]
    agent_store.ensure_approval_assistant()  # 幂等
    after = client.get("/api/v1/agents/catalog", headers=_admin()).json()["data"]["total"]
    assert before == after, "审批助手不应改变 115 业务口径"
    # 普通 /agents 列表可见（global published），但不在 catalog 的 categories 里
    cat = client.get("/api/v1/agents/catalog", headers=_admin()).json()["data"]
    flat = [ag for node in cat["categories"] for g in node["groups"] for ag in g["agents"]]
    assert agent_store.APPROVAL_ASSISTANT_ID not in [x["agent_id"] for x in flat]
